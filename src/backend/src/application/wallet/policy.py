from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from src.application.user.entity import UserId
from src.application.wallet.entity import (
    BusinessSource,
    LedgerEntry,
    LedgerEntryId,
    Reservation,
    ReservationId,
    ReservationState,
    SourceKind,
    Wallet,
    WalletChange,
    require_aware,
)
from src.application.wallet.errors import (
    InsufficientFundsError,
    RewardNotEligibleError,
    WalletConflictError,
    WalletInvariantError,
)

WELCOME_CREDIT = 10
SUCCESSFUL_CASE_CREDIT = 3
REVIEW_CREDIT = 5
REVIEW_KARMA = 1
PRIORITY_COST = 5


def _valid_score(score: object, *, minimum: int = 0) -> bool:
    """Validate runtime DTO inputs without coercion or nonfinite comparisons."""
    return isinstance(score, Decimal) and score.is_finite() and minimum <= score <= 100


@dataclass(frozen=True, kw_only=True, slots=True)
class QualityConfirmation:
    """Server-derived proof, not a client claim or a global expert grade.

    T09/T10 must validate current expert rights in every case skill under their
    locks. These IDs record the validated coverage, not an authorization lookup.
    Quality is an exact finite Decimal score, not integer wallet units.
    """

    revision_id: UUID
    expert_id: UserId
    confirmed_skill_ids: frozenset[UUID]
    quality_score: Decimal


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationEvidence:
    """T09-owned publication facts, mapped here without importing review entities.

    Caller validates score/verdict consistency, meaningful feedback and current
    per-skill review rights. No public request may be deserialized into this DTO.
    Scores remain exact finite Decimals; eligibility never rounds or truncates.
    """

    review_id: UUID
    revision_id: UUID
    case_id: UUID
    author_id: UserId
    reviewer_id: UserId
    case_skill_ids: frozenset[UUID]
    published: bool
    valid_publication: bool
    approved: bool
    score: Decimal
    quality_required: bool
    confirmation: QualityConfirmation | None = None

    def require_qualifying(self) -> None:
        if (
            not self.published
            or not self.valid_publication
            or not self.case_skill_ids
            or self.author_id == self.reviewer_id
            or not _valid_score(self.score)
        ):
            raise RewardNotEligibleError("A valid published review is required")
        if self.quality_required:
            confirmation = self.confirmation
            if (
                confirmation is None
                or confirmation.revision_id != self.revision_id
                or confirmation.expert_id in {self.author_id, self.reviewer_id}
                or not self.case_skill_ids <= confirmation.confirmed_skill_ids
                or not _valid_score(confirmation.quality_score, minimum=70)
            ):
                raise RewardNotEligibleError("Independent confirmation of this revision in every skill is required")


@dataclass(frozen=True, kw_only=True, slots=True)
class PriorityJob:
    """Caller attests ownership and waiting primary state while holding the job lock."""

    job_id: UUID
    owner_id: UserId
    waiting_primary: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class Allocation:
    """Caller holds job/assignment locks and validates all current matching rights."""

    job_id: UUID
    assignment_id: UUID
    eligible: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class ExpiredAllocation:
    """Caller has atomically marked this lease expired, rejecting stale completion."""

    job_id: UUID
    assignment_id: UUID
    lease_expires_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class CancelledAllocation:
    """Caller atomically cancelled this assignment and requeued its job under locks."""

    job_id: UUID
    assignment_id: UUID


def _change(
    wallet: Wallet,
    source: BusinessSource,
    now: datetime,
    *,
    settled: int = 0,
    reserved: int = 0,
    karma: int = 0,
    related_entry_id: LedgerEntryId | None = None,
    reservation_id: ReservationId | None = None,
    job_id: UUID | None = None,
    assignment_id: UUID | None = None,
    case_id: UUID | None = None,
    revision_id: UUID | None = None,
) -> WalletChange:
    after = replace(
        wallet,
        settled=wallet.settled + settled,
        reserved=wallet.reserved + reserved,
        karma=wallet.karma + karma,
        version=wallet.version + 1,
    )
    entry = LedgerEntry(
        id=LedgerEntryId(uuid4()),
        owner_id=wallet.owner_id,
        source=source,
        created_at=now,
        sequence=after.version,
        settled_delta=settled,
        reserved_delta=reserved,
        karma_delta=karma,
        related_entry_id=related_entry_id,
        reservation_id=reservation_id,
        job_id=job_id,
        assignment_id=assignment_id,
        case_id=case_id,
        origin_revision_id=revision_id,
    )
    return WalletChange(before=wallet, after=after, entry=entry)


def welcome(wallet: Wallet, *, now: datetime) -> WalletChange:
    return _change(
        wallet,
        BusinessSource(kind=SourceKind.WELCOME, subject_id=wallet.owner_id),
        now,
        settled=WELCOME_CREDIT,
    )


def reward_case(wallet: Wallet, evidence: PublicationEvidence, *, now: datetime) -> WalletChange:
    evidence.require_qualifying()
    if wallet.owner_id != evidence.author_id or not evidence.approved or evidence.score < 70:
        raise RewardNotEligibleError("Successful case requires finalized approval scoring at least 70")
    return _change(
        wallet,
        BusinessSource(kind=SourceKind.SUCCESSFUL_CASE, subject_id=evidence.case_id, scope_id=wallet.owner_id),
        now,
        settled=SUCCESSFUL_CASE_CREDIT,
        case_id=evidence.case_id,
        revision_id=evidence.revision_id,
    )


def reward_review(wallet: Wallet, evidence: PublicationEvidence, *, now: datetime) -> WalletChange:
    evidence.require_qualifying()
    if wallet.owner_id != evidence.reviewer_id:
        raise RewardNotEligibleError("Review reward belongs to its reviewer")
    return _change(
        wallet,
        BusinessSource(kind=SourceKind.REVIEW_REWARD, subject_id=evidence.review_id),
        now,
        settled=REVIEW_CREDIT,
        karma=REVIEW_KARMA,
        case_id=evidence.case_id,
        revision_id=evidence.revision_id,
    )


def reserve(wallet: Wallet, reservation_id: ReservationId, job: PriorityJob, *, now: datetime) -> WalletChange:
    if wallet.owner_id != job.owner_id or not job.waiting_primary:
        raise WalletConflictError("Priority requires an owned waiting primary job")
    if wallet.available < PRIORITY_COST:
        raise InsufficientFundsError("Priority requires five available units")
    change = _change(
        wallet,
        BusinessSource(kind=SourceKind.RESERVE, subject_id=reservation_id),
        now,
        reserved=PRIORITY_COST,
        reservation_id=reservation_id,
        job_id=job.job_id,
    )
    return replace(
        change,
        reservation_after=Reservation(
            id=reservation_id,
            owner_id=wallet.owner_id,
            job_id=job.job_id,
            state=ReservationState.HELD,
            reserve_entry_id=change.entry.id,
            created_at=now,
        ),
    )


def _require_hold(wallet: Wallet, hold: Reservation, state: ReservationState) -> None:
    if wallet.owner_id != hold.owner_id or hold.state != state:
        raise WalletConflictError("Reservation is not in the required state")
    if state == ReservationState.HELD and wallet.reserved < PRIORITY_COST:
        raise WalletInvariantError("Active reservation is not backed by wallet reserved balance")


def settle(wallet: Wallet, hold: Reservation, allocation: Allocation, *, now: datetime) -> WalletChange:
    _require_hold(wallet, hold, ReservationState.HELD)
    if not allocation.eligible or allocation.job_id != hold.job_id:
        raise WalletConflictError("Priority can settle only at eligible allocation of the same job")
    change = _change(
        wallet,
        BusinessSource(kind=SourceKind.SETTLE, subject_id=hold.id),
        now,
        settled=-PRIORITY_COST,
        reserved=-PRIORITY_COST,
        related_entry_id=hold.reserve_entry_id,
        reservation_id=hold.id,
        job_id=hold.job_id,
        assignment_id=allocation.assignment_id,
    )
    return replace(
        change,
        reservation_before=hold,
        reservation_after=replace(
            hold,
            state=ReservationState.SETTLED,
            assignment_id=allocation.assignment_id,
            settlement_entry_id=change.entry.id,
        ),
    )


def release(wallet: Wallet, hold: Reservation, *, now: datetime) -> WalletChange:
    _require_hold(wallet, hold, ReservationState.HELD)
    change = _change(
        wallet,
        BusinessSource(kind=SourceKind.RELEASE, subject_id=hold.id),
        now,
        reserved=-PRIORITY_COST,
        related_entry_id=hold.reserve_entry_id,
        reservation_id=hold.id,
        job_id=hold.job_id,
    )
    return replace(
        change,
        reservation_before=hold,
        reservation_after=replace(hold, state=ReservationState.RELEASED, terminal_entry_id=change.entry.id),
    )


def refund(
    wallet: Wallet, hold: Reservation, expiry: ExpiredAllocation | CancelledAllocation, *, now: datetime
) -> WalletChange:
    _require_hold(wallet, hold, ReservationState.SETTLED)
    require_aware(now)
    if isinstance(expiry, ExpiredAllocation):
        require_aware(expiry.lease_expires_at)
        if now < expiry.lease_expires_at:
            raise WalletConflictError("Refund requires expiry of the charged assignment")
    if expiry.assignment_id != hold.assignment_id or expiry.job_id != hold.job_id:
        raise WalletConflictError("Refund requires termination of the charged assignment")
    change = _change(
        wallet,
        BusinessSource(kind=SourceKind.REFUND, subject_id=hold.id),
        now,
        settled=PRIORITY_COST,
        related_entry_id=hold.settlement_entry_id,
        reservation_id=hold.id,
        job_id=hold.job_id,
        assignment_id=hold.assignment_id,
    )
    return replace(
        change,
        reservation_before=hold,
        reservation_after=replace(hold, state=ReservationState.REFUNDED, terminal_entry_id=change.entry.id),
    )
