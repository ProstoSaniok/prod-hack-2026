import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.application.user.entity import UserId
from src.application.wallet.entity import (
    ReservationId,
    ReservationState,
)
from src.application.wallet.errors import (
    ReservationNotFoundError,
    RewardNotEligibleError,
    WalletConflictError,
    WalletInvariantError,
    WalletWriteConflictError,
)
from src.application.wallet.policy import (
    Allocation,
    ExpiredAllocation,
    PriorityJob,
    PublicationEvidence,
)
from src.application.wallet.service import WalletCollaborator
from tests.unit.wallet.memory import MemoryDatabase

NOW = datetime(2026, 10, 16, tzinfo=UTC)
OWNER = UserId(UUID(int=1))
REVIEWER = UserId(UUID(int=2))


def evidence() -> PublicationEvidence:
    return PublicationEvidence(
        review_id=uuid4(),
        revision_id=uuid4(),
        case_id=uuid4(),
        author_id=OWNER,
        reviewer_id=REVIEWER,
        case_skill_ids=frozenset({uuid4()}),
        published=True,
        valid_publication=True,
        approved=True,
        score=Decimal(70),
        quality_required=False,
    )


def job() -> PriorityJob:
    return PriorityJob(job_id=uuid4(), owner_id=OWNER, waiting_primary=True)


async def funded_database() -> MemoryDatabase:
    database = MemoryDatabase()
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER,))
        await collaborator.welcome(OWNER, now=NOW)
    return database


async def test_rewards_replay_after_new_transaction_and_changed_revision() -> None:
    database = MemoryDatabase()
    publication = evidence()
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(REVIEWER, OWNER))
        assert transaction.lock_order == (OWNER, REVIEWER)
        welcome = await collaborator.welcome(OWNER, now=NOW)
        case = await collaborator.reward_case(publication, now=NOW)
        review = await collaborator.reward_review(publication, now=NOW)
        assert database.commits == 0  # Collaborator did not commit.
        assert not database.entries
        assert (await transaction.get_wallet(REVIEWER)).karma == 1
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER, REVIEWER))
        later = NOW + timedelta(days=365)
        revision = replace(publication, revision_id=uuid4())
        assert await collaborator.welcome(OWNER, now=later) == welcome
        assert await collaborator.reward_case(revision, now=later) == case
        assert await collaborator.reward_review(revision, now=later) == review
        # A new review of the same case still cannot earn another successful-case reward.
        assert await collaborator.reward_case(replace(revision, review_id=uuid4()), now=later) == case
    assert len(database.entries) == 3
    assert database.wallets[OWNER].settled == 13
    assert (database.wallets[REVIEWER].settled, database.wallets[REVIEWER].karma) == (5, 1)
    assert review.origin_revision_id == publication.revision_id


async def test_review_source_cannot_be_reassigned_or_retargeted() -> None:
    database = MemoryDatabase()
    publication = evidence()
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(REVIEWER,))
        await collaborator.reward_review(publication, now=NOW)
    stranger = UserId(uuid4())
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(stranger, REVIEWER))
        with pytest.raises(WalletConflictError):
            await collaborator.reward_review(replace(publication, reviewer_id=stranger), now=NOW)
        with pytest.raises(WalletConflictError):
            await collaborator.reward_review(replace(publication, case_id=uuid4()), now=NOW)
    assert database.wallets[stranger].settled == 0
    assert len(database.entries) == 1


async def test_spent_review_reward_is_not_clawed_back_on_reassessment() -> None:
    database = MemoryDatabase()
    publication = evidence()
    priority = replace(job(), owner_id=REVIEWER)
    hold_id = ReservationId(uuid4())
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(REVIEWER,))
        original = await collaborator.reward_review(publication, now=NOW)
        await collaborator.reserve(hold_id, priority, now=NOW)
        await collaborator.settle(
            REVIEWER, hold_id, Allocation(job_id=priority.job_id, assignment_id=uuid4(), eligible=True), now=NOW
        )
        assert (await transaction.get_wallet(REVIEWER)).settled == 0
        assert await collaborator.reward_review(replace(publication, published=False), now=NOW) == original
    assert database.wallets[REVIEWER].karma == 1
    assert len(database.entries) == 3


async def test_finalization_failure_rolls_back_both_wallets_and_karma() -> None:
    database = MemoryDatabase()
    publication = evidence()

    async def finalize() -> None:
        async with database.transaction() as transaction:
            collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER, REVIEWER))
            await collaborator.reward_case(publication, now=NOW)
            await collaborator.reward_review(publication, now=NOW)
            raise RuntimeError("downstream finalization failed")

    with pytest.raises(RuntimeError, match="downstream finalization failed"):
        await finalize()
    assert not database.wallets
    assert not database.entries
    assert database.commits == 0
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER, REVIEWER))
        await collaborator.reward_case(publication, now=NOW)
        await collaborator.reward_review(publication, now=NOW)
    assert len(database.entries) == 2
    assert database.wallets[REVIEWER].karma == 1


async def test_pending_checks_cannot_start_rewards() -> None:
    database = MemoryDatabase()
    publication = replace(evidence(), quality_required=True)
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER, REVIEWER))
        with pytest.raises(RewardNotEligibleError):
            await collaborator.reward_case(publication, now=NOW)
        with pytest.raises(RewardNotEligibleError):
            await collaborator.reward_review(publication, now=NOW)
    assert not database.entries
    assert all(wallet.version == 0 for wallet in database.wallets.values())


async def test_priority_lifecycle_and_replay_never_regress_state() -> None:
    database = await funded_database()
    priority = job()
    reservation_id = ReservationId(uuid4())
    allocation = Allocation(job_id=priority.job_id, assignment_id=uuid4(), eligible=True)
    expiry = ExpiredAllocation(job_id=priority.job_id, assignment_id=allocation.assignment_id, lease_expires_at=NOW)
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER,))
        reserve_entry = await collaborator.reserve(reservation_id, priority, now=NOW)
        wallet = await transaction.get_wallet(OWNER)
        assert (wallet.settled, wallet.reserved, wallet.available) == (10, 5, 5)
        settlement = await collaborator.settle(OWNER, reservation_id, allocation, now=NOW)
        wallet = await transaction.get_wallet(OWNER)
        assert (wallet.settled, wallet.reserved, wallet.available) == (5, 0, 5)
        refund = await collaborator.refund(OWNER, reservation_id, expiry, now=NOW)
        wallet = await transaction.get_wallet(OWNER)
        assert (wallet.settled, wallet.reserved, wallet.available) == (10, 0, 10)
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER,))
        assert await collaborator.reserve(reservation_id, priority, now=NOW) == reserve_entry
        assert await collaborator.settle(OWNER, reservation_id, allocation, now=NOW) == settlement
        assert await collaborator.refund(OWNER, reservation_id, expiry, now=NOW) == refund
        with pytest.raises(WalletConflictError):
            await collaborator.release(OWNER, reservation_id, now=NOW)
    hold = database.reservations[reservation_id]
    assert hold.state == ReservationState.REFUNDED
    assert settlement.related_entry_id == reserve_entry.id
    assert refund.related_entry_id == settlement.id
    assert hold.settlement_entry_id == settlement.id
    assert hold.terminal_entry_id == refund.id
    assert [entry.sequence for entry in database.entries.values()] == [1, 2, 3, 4]
    assert len(database.entries) == 4


async def test_release_replay_and_active_job_uniqueness() -> None:
    database = await funded_database()
    priority = job()
    reservation_id = ReservationId(uuid4())
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER,))
        reserve_entry = await collaborator.reserve(reservation_id, priority, now=NOW)
        with pytest.raises(WalletConflictError):
            await collaborator.reserve(ReservationId(uuid4()), priority, now=NOW)
        released = await collaborator.release(OWNER, reservation_id, now=NOW)
        assert released.related_entry_id == reserve_entry.id
        assert await collaborator.release(OWNER, reservation_id, now=NOW) == released
        assert await collaborator.reserve(reservation_id, priority, now=NOW) == reserve_entry
        with pytest.raises(WalletConflictError):
            await collaborator.settle(
                OWNER, reservation_id, Allocation(job_id=priority.job_id, assignment_id=uuid4(), eligible=True), now=NOW
            )
    assert database.reservations[reservation_id].state == ReservationState.RELEASED
    assert database.wallets[OWNER].available == 10
    assert len(database.entries) == 3


async def test_changed_reserve_and_assignment_replay_conflict() -> None:
    database = await funded_database()
    priority = job()
    reservation_id = ReservationId(uuid4())
    allocation = Allocation(job_id=priority.job_id, assignment_id=uuid4(), eligible=True)
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER,))
        await collaborator.reserve(reservation_id, priority, now=NOW)
        with pytest.raises(WalletConflictError):
            await collaborator.reserve(reservation_id, replace(priority, job_id=uuid4()), now=NOW)
        await collaborator.settle(OWNER, reservation_id, allocation, now=NOW)
        with pytest.raises(WalletConflictError):
            await collaborator.settle(OWNER, reservation_id, replace(allocation, assignment_id=uuid4()), now=NOW)


async def test_missing_and_foreign_reservations_are_indistinguishable() -> None:
    database = await funded_database()
    reservation_id = ReservationId(uuid4())
    async with database.transaction() as transaction:
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(OWNER, REVIEWER))
        await collaborator.reserve(reservation_id, job(), now=NOW)
        for missing_id in (reservation_id, ReservationId(uuid4())):
            with pytest.raises(ReservationNotFoundError, match="Reservation not found"):
                await collaborator.release(REVIEWER, missing_id, now=NOW)


async def test_lock_scope_is_explicit_and_sorted() -> None:
    database = MemoryDatabase()
    async with database.transaction() as transaction:
        with pytest.raises(WalletInvariantError):
            await WalletCollaborator.acquire(transaction, owner_ids=())
        collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(REVIEWER, OWNER, REVIEWER))
        assert transaction.lock_order == (OWNER, REVIEWER)
        with pytest.raises(WalletInvariantError):
            await collaborator.welcome(UserId(uuid4()), now=NOW)


async def test_cross_wallet_source_race_aborts_one_whole_fake_transaction() -> None:
    database = MemoryDatabase()
    publication = evidence()
    other_reviewer = UserId(uuid4())
    barrier = asyncio.Barrier(2)

    async def publish(reviewer: UserId) -> bool:
        try:
            async with database.transaction() as transaction:
                collaborator = await WalletCollaborator.acquire(transaction, owner_ids=(reviewer,))
                await collaborator.reward_review(replace(publication, reviewer_id=reviewer), now=NOW)
                # Both have staged the same global review source under different wallet locks.
                await barrier.wait()
        except WalletWriteConflictError:
            return False
        return True

    outcomes = await asyncio.wait_for(asyncio.gather(publish(REVIEWER), publish(other_reviewer)), timeout=2)
    assert sum(outcomes) == 1
    assert len(database.entries) == 1
    assert sum(wallet.settled for wallet in database.wallets.values()) == 5
    assert sum(wallet.karma for wallet in database.wallets.values()) == 1
