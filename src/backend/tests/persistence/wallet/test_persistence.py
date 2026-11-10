import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from psycopg import Error as PostgreSQLError
from sqlalchemy import Engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.sql import Executable

from src.adapters.data_gateways.tables import meta_data
from src.adapters.wallet.persistence import BIGINT_MAX, SqlAlchemyWalletTransaction
from src.application.user.entity import UserId
from src.application.wallet import policy
from src.application.wallet.entity import BusinessSource, LedgerEntry, ReservationId, ReservationState, SourceKind
from src.application.wallet.errors import (
    InsufficientFundsError,
    WalletConflictError,
    WalletInvariantError,
    WalletWriteConflictError,
)
from src.application.wallet.policy import Allocation, ExpiredAllocation, PriorityJob, PublicationEvidence
from src.application.wallet.service import WalletCollaborator
from tests.persistence.helpers import Subject, claim, seed_review, seed_subject

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def seed(engine: Engine) -> tuple[Subject, PublicationEvidence]:
    with engine.begin() as connection:
        subject = seed_subject(connection)
        review, revision = seed_review(connection, subject)
    return subject, PublicationEvidence(
        review_id=review,
        revision_id=revision,
        case_id=subject.case,
        author_id=UserId(subject.author),
        reviewer_id=UserId(subject.reviewer),
        case_skill_ids=frozenset({subject.competency}),
        published=True,
        valid_publication=True,
        approved=True,
        score=Decimal("70.001"),
        quality_required=False,
    )


async def assert_reconciled(engine: AsyncEngine, owner: UserId) -> None:
    wallets, ledger, holds = (meta_data.tables[name] for name in ("wallets", "wallet_ledger", "wallet_reservations"))
    async with engine.connect() as connection:
        wallet = (await connection.execute(select(wallets).where(wallets.c.owner_id == owner))).mappings().one()
        entries = (
            (await connection.execute(select(ledger).where(ledger.c.owner_id == owner).order_by(ledger.c.sequence)))
            .mappings()
            .all()
        )
        assert wallet["settled"] == sum(row["settled_delta"] for row in entries)
        assert wallet["reserved"] == sum(row["reserved_delta"] for row in entries)
        assert wallet["karma"] == sum(row["karma_delta"] for row in entries)
        assert [row["sequence"] for row in entries] == list(range(1, wallet["version"] + 1))
        active = await connection.scalar(
            select(func.count())
            .select_from(holds)
            .where(
                holds.c.owner_id == owner,
                holds.c.state == ReservationState.HELD.name,
            )
        )
        assert active is not None
        assert wallet["reserved"] == active * 5


async def test_rewards_and_priority_round_trip_replay_after_reconnect(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    owner, reviewer = evidence.author_id, evidence.reviewer_id
    factory = async_sessionmaker(wallet_engine)
    reservation_id = ReservationId(uuid4())
    job = PriorityJob(job_id=subject.job, owner_id=owner, waiting_primary=True)
    with core_engine.begin() as connection:
        assignment_id = claim(connection, subject, state="EXPIRED")
    allocation = Allocation(job_id=subject.job, assignment_id=assignment_id, eligible=True)
    expiry = ExpiredAllocation(job_id=subject.job, assignment_id=assignment_id, lease_expires_at=NOW)
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(reviewer, owner))
        welcome = await collaborator.welcome(owner, now=NOW)
        case = await collaborator.reward_case(evidence, now=NOW)
        review = await collaborator.reward_review(evidence, now=NOW)
        reserved = await collaborator.reserve(reservation_id, job, now=NOW)
        assert (await adapter.get_wallet(owner)).available == 8
        with pytest.raises(WalletConflictError, match="active priority"):
            await collaborator.reserve(ReservationId(uuid4()), job, now=NOW)
        settled = await collaborator.settle(owner, reservation_id, allocation, now=NOW)
        assert (await adapter.get_wallet(owner)).settled == 8
        assert (await adapter.active_reservation(subject.job)) is not None
        with pytest.raises(WalletConflictError, match="active priority"):
            await collaborator.reserve(ReservationId(uuid4()), job, now=NOW)
        # Independent connection cannot see any wallet effects until the caller commits.
        async with wallet_engine.connect() as observer:
            assert await observer.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"])) == 0

    barrier = asyncio.Barrier(2)

    async def refund() -> LedgerEntry:
        await barrier.wait()
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(owner,))
            entry = await collaborator.refund(owner, reservation_id, expiry, now=NOW)
            assert await adapter.active_reservation(subject.job) is None
            return entry

    refunds = await asyncio.wait_for(asyncio.gather(refund(), refund()), timeout=10)
    refunded = refunds[0]
    assert refunds[1] == refunded
    await wallet_engine.dispose()  # Fresh physical connections, no adapter/session identity cache.
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(owner, reviewer))
        assert await collaborator.welcome(owner, now=NOW + timedelta(days=365)) == welcome
        assert await collaborator.reward_case(evidence, now=NOW) == case
        assert await collaborator.reward_review(evidence, now=NOW) == review
        assert await collaborator.reserve(reservation_id, job, now=NOW) == reserved
        assert await collaborator.settle(owner, reservation_id, allocation, now=NOW) == settled
        assert await collaborator.refund(owner, reservation_id, expiry, now=NOW) == refunded
        assert (await adapter.get_wallet(owner)).settled == 13
        assert await adapter.get_reservation(reviewer, reservation_id) is None
        hold = await adapter.get_reservation(owner, reservation_id)
        assert hold is not None
        assert hold.state == ReservationState.REFUNDED
        assert await adapter.reconcile(owner) == await adapter.get_wallet(owner)
        assert (await adapter.reconcile(reviewer)).karma == 1

    async def duplicate_scoped_reward() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            await adapter.lock_wallets((owner,))
            await adapter.apply(policy.reward_case(await adapter.get_wallet(owner), evidence, now=NOW))

    with pytest.raises(WalletWriteConflictError):
        await duplicate_scoped_reward()
    await assert_reconciled(wallet_engine, owner)
    await assert_reconciled(wallet_engine, reviewer)


async def test_outer_failure_rolls_back_currency_karma_and_hold(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    factory = async_sessionmaker(wallet_engine)

    async def finalize() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            collaborator = await WalletCollaborator.acquire(
                adapter, owner_ids=(evidence.author_id, evidence.reviewer_id)
            )
            await collaborator.welcome(evidence.author_id, now=NOW)
            await collaborator.reward_review(evidence, now=NOW)
            await collaborator.reserve(
                ReservationId(uuid4()),
                PriorityJob(job_id=subject.job, owner_id=evidence.author_id, waiting_primary=True),
                now=NOW,
            )
            raise RuntimeError("downstream failure")

    with pytest.raises(RuntimeError, match="downstream failure"):
        await finalize()
    async with wallet_engine.connect() as connection:
        for name in ("wallet_owner_guards", "wallets", "wallet_ledger", "wallet_reservations"):
            assert await connection.scalar(select(func.count()).select_from(meta_data.tables[name])) == 0


async def test_absent_owner_guards_serialize_welcome_and_reversed_beneficiaries(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    _, evidence = seed(core_engine)
    factory = async_sessionmaker(wallet_engine)
    barrier = asyncio.Barrier(2)

    async def welcome(owners: tuple[UserId, ...]) -> UUID:
        await barrier.wait()
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            collaborator = await WalletCollaborator.acquire(adapter, owner_ids=owners)
            result = await collaborator.welcome(evidence.author_id, now=NOW)
            await collaborator.welcome(evidence.reviewer_id, now=NOW)
            return result.id

    owners = (evidence.author_id, evidence.reviewer_id)
    results = await asyncio.wait_for(asyncio.gather(welcome(owners), welcome(tuple(reversed(owners)))), timeout=10)
    assert results[0] == results[1]
    for owner in owners:
        await assert_reconciled(wallet_engine, owner)


async def test_concurrent_spend_locks_balance_and_job_uniqueness(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    owner = evidence.author_id
    factory = async_sessionmaker(wallet_engine)
    jobs = [PriorityJob(job_id=subject.job, owner_id=owner, waiting_primary=True)]
    with core_engine.begin() as connection:
        for _ in range(2):
            additional = seed_subject(connection)
            connection.execute(
                update(meta_data.tables["projects"])
                .where(meta_data.tables["projects"].c.id == additional.project)
                .values(participant_id=owner)
            )
            connection.execute(
                update(meta_data.tables["repositories"])
                .where(meta_data.tables["repositories"].c.id == additional.repository)
                .values(owner_id=owner)
            )
            jobs.append(PriorityJob(job_id=additional.job, owner_id=owner, waiting_primary=True))
    async with factory.begin() as session:
        collaborator = await WalletCollaborator.acquire(SqlAlchemyWalletTransaction(session), owner_ids=(owner,))
        await collaborator.welcome(owner, now=NOW)
    barrier = asyncio.Barrier(3)

    async def reserve(job: PriorityJob) -> bool:
        await barrier.wait()
        try:
            async with factory.begin() as session:
                collaborator = await WalletCollaborator.acquire(
                    SqlAlchemyWalletTransaction(session), owner_ids=(owner,)
                )
                await collaborator.reserve(ReservationId(uuid4()), job, now=NOW)
        except InsufficientFundsError:
            return False
        return True

    assert sum(await asyncio.wait_for(asyncio.gather(*(reserve(job) for job in jobs)), timeout=10)) == 2
    await assert_reconciled(wallet_engine, owner)


async def test_wallet_and_reservation_actual_cas_failures_roll_back_staged_entries(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    owner = evidence.author_id
    factory = async_sessionmaker(wallet_engine)
    hold_id = ReservationId(uuid4())
    job = PriorityJob(job_id=subject.job, owner_id=owner, waiting_primary=True)
    async with factory.begin() as session:
        collaborator = await WalletCollaborator.acquire(SqlAlchemyWalletTransaction(session), owner_ids=(owner,))
        await collaborator.welcome(owner, now=NOW)

    async def stale_wallet() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            await adapter.lock_wallets((owner,))
            stale = replace(await adapter.get_wallet(owner), settled=20)
            await adapter.apply(policy.reserve(stale, hold_id, job, now=NOW))

    with pytest.raises(WalletWriteConflictError, match="CAS failed"):
        await stale_wallet()
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(owner,))
        assert await adapter.get_reservation(owner, hold_id) is None
        await collaborator.reserve(hold_id, job, now=NOW)

    async def stale_hold() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            await adapter.lock_wallets((owner,))
            hold = await adapter.get_reservation(owner, hold_id)
            assert hold is not None
            stale = replace(hold, created_at=hold.created_at - timedelta(seconds=1))
            await adapter.apply(policy.release(await adapter.get_wallet(owner), stale, now=NOW))

    with pytest.raises(WalletWriteConflictError, match="CAS failed"):
        await stale_hold()
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(owner,))
        assert (await adapter.get_wallet(owner)).version == 2
        await collaborator.release(owner, hold_id, now=NOW)
    await assert_reconciled(wallet_engine, owner)


async def test_global_null_safe_sources_across_wallets_and_sql_immutability(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    _, evidence = seed(core_engine)
    factory = async_sessionmaker(wallet_engine)
    async with factory.begin() as session:
        collaborator = await WalletCollaborator.acquire(
            SqlAlchemyWalletTransaction(session), owner_ids=(evidence.reviewer_id, evidence.author_id)
        )
        reward = await collaborator.reward_review(evidence, now=NOW)
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        await adapter.lock_wallets((evidence.author_id,))
        assert await adapter.find_entry(reward.source) == reward  # No recipient filter in global lookup.
    ledger = meta_data.tables["wallet_ledger"]
    async with wallet_engine.connect() as connection:
        row = dict((await connection.execute(select(ledger).where(ledger.c.id == reward.id))).mappings().one())
    row.update(id=uuid4(), owner_id=evidence.author_id)

    async def duplicate() -> None:
        async with factory.begin() as session:
            await session.execute(insert(ledger).values(**row))

    with pytest.raises(DBAPIError) as duplicate_error:
        await duplicate()
    assert isinstance(duplicate_error.value.orig, PostgreSQLError)
    assert duplicate_error.value.orig.diag.constraint_name == "uq_wallet_source_unscoped"

    async def mutate(statement: Executable) -> None:
        async with factory.begin() as session:
            await session.execute(statement)

    for statement in (
        update(ledger).values(karma_delta=0),
        ledger.delete(),
        text("TRUNCATE wallet_ledger CASCADE"),
    ):
        with pytest.raises(DBAPIError) as mutation_error:
            await mutate(statement)
        assert isinstance(mutation_error.value.orig, PostgreSQLError)
        assert mutation_error.value.orig.sqlstate == "55000"
    await assert_reconciled(wallet_engine, evidence.reviewer_id)


async def test_bigint_overflow_and_transaction_scope_fail_closed(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    _, evidence = seed(core_engine)
    owner = evidence.author_id
    factory = async_sessionmaker(wallet_engine)
    async with factory() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        with pytest.raises(WalletInvariantError, match="active UoW"):
            await adapter.lock_wallets((owner,))
    async with factory.begin() as session:
        adapter = SqlAlchemyWalletTransaction(session)
        await adapter.lock_wallets((owner,))
        # Test-only capacity boundary snapshot, not a synthetic historical credit.
        await session.execute(
            update(meta_data.tables["wallets"])
            .values(settled=BIGINT_MAX)
            .where(
                meta_data.tables["wallets"].c.owner_id == owner,
            )
        )
        before = await adapter.get_wallet(owner)
        with pytest.raises(WalletInvariantError, match="BIGINT"):
            await adapter.apply(policy.welcome(before, now=NOW))
        assert await adapter.get_wallet(owner) == before
        assert await adapter.find_entry(BusinessSource(kind=SourceKind.WELCOME, subject_id=owner)) is None
        # Restore test-only snapshot so the persisted wallet reconciles to empty history.
        await session.execute(
            update(meta_data.tables["wallets"])
            .values(settled=0)
            .where(
                meta_data.tables["wallets"].c.owner_id == owner,
            )
        )
    async with session, session.begin():
        with pytest.raises(WalletInvariantError, match="original active UoW"):
            await adapter.get_wallet(owner)


async def test_semantic_reward_and_priority_provenance_rejected_before_writes(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    owner = evidence.author_id
    factory = async_sessionmaker(wallet_engine)

    async def wrong_review_source() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            await adapter.lock_wallets((evidence.reviewer_id,))
            await adapter.apply(
                policy.reward_review(
                    await adapter.get_wallet(evidence.reviewer_id),
                    replace(evidence, review_id=uuid4()),
                    now=NOW,
                )
            )

    with pytest.raises(WalletInvariantError, match="underlying review"):
        await wrong_review_source()

    async def wrong_owner() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            await adapter.lock_wallets((owner,))
            await adapter.apply(
                policy.reward_review(
                    await adapter.get_wallet(owner),
                    replace(evidence, reviewer_id=owner, author_id=UserId(subject.expert)),
                    now=NOW,
                )
            )

    with pytest.raises(WalletInvariantError, match="beneficiary"):
        await wrong_owner()

    async def wrong_job_owner() -> None:
        async with factory.begin() as session:
            adapter = SqlAlchemyWalletTransaction(session)
            collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(evidence.reviewer_id,))
            await collaborator.welcome(evidence.reviewer_id, now=NOW)
            await collaborator.reserve(
                ReservationId(uuid4()),
                PriorityJob(job_id=subject.job, owner_id=evidence.reviewer_id, waiting_primary=True),
                now=NOW,
            )

    with pytest.raises(WalletInvariantError, match="job does not belong"):
        await wrong_job_owner()
    async with wallet_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"])) == 0


async def test_wallet_cycle_check_is_schema_qualified_and_leaves_request_cycle_deferred(
    core_engine: Engine, wallet_engine: AsyncEngine
) -> None:
    subject, evidence = seed(core_engine)
    async with wallet_engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str)
    translated = wallet_engine.execution_options(schema_translate_map={None: schema})
    factory = async_sessionmaker(translated)
    scope_id, generation_id = uuid4(), uuid4()
    async with factory.begin() as session:
        # All Core tables now require schema translation; no incidental search_path resolution.
        await session.execute(text("SET LOCAL search_path TO public"))
        await session.execute(
            insert(meta_data.tables["request_scopes"]).values(
                id=scope_id,
                actor_id=evidence.author_id,
                method="POST",
                path="/api/v1/submissions/priority-requests",
                request_key="wallet-cycle-check",
                current_request_id=generation_id,
            )
        )
        adapter = SqlAlchemyWalletTransaction(session)
        collaborator = await WalletCollaborator.acquire(adapter, owner_ids=(evidence.author_id,))
        await collaborator.welcome(evidence.author_id, now=NOW)
        await collaborator.reserve(
            ReservationId(uuid4()),
            PriorityJob(job_id=subject.job, owner_id=evidence.author_id, waiting_primary=True),
            now=NOW,
        )
        # Wallet applies succeeded while the independent caller-owned FK was incomplete.
        await session.execute(
            insert(meta_data.tables["request_generations"]).values(
                id=generation_id,
                scope_id=scope_id,
                payload_hash="a" * 64,
                created_at=NOW,
                expires_at=NOW + timedelta(days=1),
            )
        )
    await assert_reconciled(wallet_engine, evidence.author_id)
