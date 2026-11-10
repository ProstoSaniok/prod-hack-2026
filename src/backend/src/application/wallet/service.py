from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID

from src.application.user.entity import UserId
from src.application.wallet import policy
from src.application.wallet.entity import BusinessSource, LedgerEntry, Reservation, ReservationId, SourceKind
from src.application.wallet.errors import ReservationNotFoundError, WalletConflictError, WalletInvariantError
from src.application.wallet.policy import (
    Allocation,
    CancelledAllocation,
    ExpiredAllocation,
    PriorityJob,
    PublicationEvidence,
)
from src.application.wallet.ports import WalletTransaction


@dataclass(frozen=True, kw_only=True, slots=True)
class WalletCollaborator:
    """Trusted application collaborator, NOT an independently committing use case.

    Acquire once after operation, domain and deterministic per-skill locks; list
    every beneficiary up front. Reuse throughout the caller's UoW. Wallet locks
    provide serialization, not authorization: T08/T09 own actor/current-rights and
    authoritative evidence checks. Nothing here accepts client-supplied proofs.
    """

    transaction: WalletTransaction
    owner_ids: tuple[UserId, ...]

    @classmethod
    async def acquire(cls, transaction: WalletTransaction, *, owner_ids: tuple[UserId, ...]) -> Self:
        ordered = tuple(sorted(set(owner_ids), key=lambda owner: owner.int))
        if not ordered:
            raise WalletInvariantError("At least one wallet owner is required")
        await transaction.lock_wallets(ordered)
        return cls(transaction=transaction, owner_ids=ordered)

    def _require_owner(self, owner_id: UserId) -> None:
        if owner_id not in self.owner_ids:
            raise WalletInvariantError("All affected wallets must be locked up front")

    async def _replay(
        self,
        source: BusinessSource,
        owner_id: UserId,
        *,
        job_id: UUID | None = None,
        assignment_id: UUID | None = None,
        case_id: UUID | None = None,
    ) -> LedgerEntry | None:
        self._require_owner(owner_id)
        entry = await self.transaction.find_entry(source)
        if entry is not None and (
            entry.owner_id != owner_id
            or entry.job_id != job_id
            or entry.assignment_id != assignment_id
            or entry.case_id != case_id
        ):
            raise WalletConflictError("Permanent business source was already used with different facts")
        return entry

    async def welcome(self, owner_id: UserId, *, now: datetime) -> LedgerEntry:
        source = BusinessSource(kind=SourceKind.WELCOME, subject_id=owner_id)
        existing = await self._replay(source, owner_id)
        if existing is not None:
            return existing
        change = policy.welcome(await self.transaction.get_wallet(owner_id), now=now)
        await self.transaction.apply(change)
        return change.entry

    async def reward_case(self, evidence: PublicationEvidence, *, now: datetime) -> LedgerEntry:
        source = BusinessSource(
            kind=SourceKind.SUCCESSFUL_CASE, subject_id=evidence.case_id, scope_id=evidence.author_id
        )
        existing = await self._replay(source, evidence.author_id, case_id=evidence.case_id)
        if existing is not None:
            return existing
        change = policy.reward_case(await self.transaction.get_wallet(evidence.author_id), evidence, now=now)
        await self.transaction.apply(change)
        return change.entry

    async def reward_review(self, evidence: PublicationEvidence, *, now: datetime) -> LedgerEntry:
        source = BusinessSource(kind=SourceKind.REVIEW_REWARD, subject_id=evidence.review_id)
        existing = await self._replay(source, evidence.reviewer_id, case_id=evidence.case_id)
        if existing is not None:
            return existing
        change = policy.reward_review(await self.transaction.get_wallet(evidence.reviewer_id), evidence, now=now)
        await self.transaction.apply(change)
        return change.entry

    async def reserve(self, reservation_id: ReservationId, job: PriorityJob, *, now: datetime) -> LedgerEntry:
        source = BusinessSource(kind=SourceKind.RESERVE, subject_id=reservation_id)
        existing = await self._replay(source, job.owner_id, job_id=job.job_id)
        if existing is not None:
            return existing
        if await self.transaction.active_reservation(job.job_id) is not None:
            raise WalletConflictError("Job already has an active priority reservation")
        change = policy.reserve(await self.transaction.get_wallet(job.owner_id), reservation_id, job, now=now)
        await self.transaction.apply(change)
        return change.entry

    async def _hold(self, owner_id: UserId, reservation_id: ReservationId) -> Reservation:
        self._require_owner(owner_id)
        hold = await self.transaction.get_reservation(owner_id, reservation_id)
        if hold is None:
            raise ReservationNotFoundError("Reservation not found")
        return hold

    async def settle(
        self, owner_id: UserId, reservation_id: ReservationId, allocation: Allocation, *, now: datetime
    ) -> LedgerEntry:
        hold = await self._hold(owner_id, reservation_id)
        existing = await self._replay(
            BusinessSource(kind=SourceKind.SETTLE, subject_id=reservation_id),
            owner_id,
            job_id=allocation.job_id,
            assignment_id=allocation.assignment_id,
        )
        if existing is not None:
            return existing
        change = policy.settle(await self.transaction.get_wallet(owner_id), hold, allocation, now=now)
        await self.transaction.apply(change)
        return change.entry

    async def release(self, owner_id: UserId, reservation_id: ReservationId, *, now: datetime) -> LedgerEntry:
        hold = await self._hold(owner_id, reservation_id)
        existing = await self._replay(
            BusinessSource(kind=SourceKind.RELEASE, subject_id=reservation_id), owner_id, job_id=hold.job_id
        )
        if existing is not None:
            return existing
        change = policy.release(await self.transaction.get_wallet(owner_id), hold, now=now)
        await self.transaction.apply(change)
        return change.entry

    async def refund(
        self,
        owner_id: UserId,
        reservation_id: ReservationId,
        expiry: ExpiredAllocation | CancelledAllocation,
        *,
        now: datetime,
    ) -> LedgerEntry:
        hold = await self._hold(owner_id, reservation_id)
        existing = await self._replay(
            BusinessSource(kind=SourceKind.REFUND, subject_id=reservation_id),
            owner_id,
            job_id=expiry.job_id,
            assignment_id=expiry.assignment_id,
        )
        if existing is not None:
            return existing
        change = policy.refund(await self.transaction.get_wallet(owner_id), hold, expiry, now=now)
        await self.transaction.apply(change)
        return change.entry
