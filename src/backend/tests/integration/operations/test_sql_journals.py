"""Real PostgreSQL evidence only after T02 metadata and the assigned DB are available."""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import override
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from src.adapters.data_gateways.tables import meta_data, user_table
from src.adapters.operations.sql_requests import SqlRequestJournal, SqlRequestOperationBindings
from src.adapters.operations.sql_work import SqlOperationJournal, SqlOutbox, SqlProjectionReceipts, database_now
from src.application.operations.entity import (
    Operation,
    OperationSpec,
    OutboxMessage,
    ProjectionSpec,
    SourceConflictError,
)
from src.application.operations.idempotency import (
    CachedResponse,
    RequestIdempotency,
    RequestJournal,
    RequestRecord,
    RequestScope,
    ResponseCapturePolicy,
)
from src.application.operations.ports import OperationJournal, Outbox, ProjectionReceipts
from src.application.operations.work import FailureCode, RetryPolicy, Work, WorkState
from src.application.user.entity import UserRole
from tests.support.idempotency import ControlledClock

TABLE_NAMES = (
    "operations",
    "request_scopes",
    "request_generations",
    "request_operation_bindings",
    "outbox_messages",
    "projection_receipts",
)


@dataclass(frozen=True)
class Database:
    sessions: async_sessionmaker[AsyncSession]
    actor: UUID
    tables: Mapping[str, Table]


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Assigned TEST_DATABASE_URL is required for real operation repository evidence")
    if any(name not in meta_data.tables for name in TABLE_NAMES):
        pytest.fail("The exact T02 operation metadata handoff must be present before database verification")
    schema = f"test_{uuid4().hex}"
    engine = create_async_engine(
        url,
        hide_parameters=True,
        execution_options={"schema_translate_map": {None: schema}},
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000 -c lock_timeout=3000"},
    )
    tables = {name: meta_data.tables[name] for name in TABLE_NAMES}
    actor = uuid4()
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(lambda sync: meta_data.create_all(sync, tables=[user_table, *tables.values()]))
            await connection.execute(
                user_table.insert().values(id=actor, created_at=func.clock_timestamp(), role=UserRole.USER)
            )
        created = True
        yield Database(async_sessionmaker(engine, expire_on_commit=False, autoflush=False), actor, tables)
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


def operation(now: datetime, source: str = "source") -> Operation:
    return Operation(
        id=uuid4(),
        spec=OperationSpec(kind="test.create", source_key=source, subject_id=uuid4(), payload_hash="0" * 64),
        work=Work(created_at=now, available_at=now),
    )


def request(now: datetime, actor: UUID) -> RequestRecord:
    return RequestRecord(
        id=uuid4(),
        scope=RequestScope(actor_id=actor, method="POST", path="/projects", key="request"),
        payload_hash="0" * 64,
        created_at=now,
        expires_at=now + timedelta(days=1),
    )


def requests(session: AsyncSession, db: Database) -> RequestJournal:
    return SqlRequestJournal(
        session,
        request_scopes_table=db.tables["request_scopes"],
        request_generations_table=db.tables["request_generations"],
    )


def bindings(session: AsyncSession, db: Database) -> SqlRequestOperationBindings:
    return SqlRequestOperationBindings(
        session,
        operations_table=db.tables["operations"],
        request_scopes_table=db.tables["request_scopes"],
        request_generations_table=db.tables["request_generations"],
        request_operation_bindings_table=db.tables["request_operation_bindings"],
    )


async def test_permanent_sources_and_receipts_share_caller_rollback(database: Database) -> None:
    db = database
    async with db.sessions() as session:
        now = await database_now(session)
        candidate = operation(now)
        projection = ProjectionSpec(
            topic="test.project", source_key="source", subject_id=candidate.spec.subject_id, revision=1
        )
        message = OutboxMessage(id=uuid4(), spec=projection, work=Work(created_at=now, available_at=now))
        operations: OperationJournal = SqlOperationJournal(session, db.tables["operations"])
        outbox: Outbox = SqlOutbox(session, db.tables["outbox_messages"])
        receipts: ProjectionReceipts = SqlProjectionReceipts(session, db.tables["projection_receipts"])
        assert await operations.reserve(candidate) == candidate
        assert await outbox.enqueue(message) == message
        assert await receipts.reserve(consumer="test.consumer", spec=projection)
        await session.rollback()
        for name in ("operations", "outbox_messages", "projection_receipts"):
            assert (await session.execute(select(func.count()).select_from(db.tables[name]))).scalar_one() == 0
        assert await operations.reserve(candidate) == candidate
        assert await outbox.enqueue(message) == message
        assert await receipts.reserve(consumer="test.consumer", spec=projection)
        await session.commit()
        assert (await operations.reserve(replace(candidate, id=uuid4()))).id == candidate.id
        assert (await outbox.enqueue(replace(message, id=uuid4()))).id == message.id
        assert not await receipts.reserve(consumer="test.consumer", spec=projection)
        with pytest.raises(SourceConflictError):
            await receipts.reserve(consumer="test.consumer", spec=replace(projection, revision=2))
        with pytest.raises(SourceConflictError):
            await operations.reserve(replace(candidate, spec=replace(candidate.spec, payload_hash="1" * 64)))
        with pytest.raises(SourceConflictError):
            await outbox.enqueue(replace(message, spec=replace(projection, revision=2)))


async def test_concurrent_scope_reservation_and_exact_retained_outcome(database: Database) -> None:
    db = database
    async with db.sessions() as session:
        now = await database_now(session)
    first = request(now, db.actor)

    async def reserve(candidate: RequestRecord) -> RequestRecord:
        async with db.sessions.begin() as session:
            return await requests(session, db).reserve(candidate, now)

    one, two = await asyncio.gather(reserve(first), reserve(replace(first, id=uuid4())))
    assert one.id == two.id
    prefix, suffix = b' { "content": "', b'" }\n'
    body = prefix + b"x" * (1048576 - len(prefix) - len(suffix)) + suffix
    captured = CachedResponse(
        status_code=201, body=body, location="/projects/one", queue_state_header=("x-QuEuE-sTaTe", "waiting")
    )
    async with db.sessions.begin() as session:
        journal = requests(session, db)
        before = await database_now(session)
        assert await journal.complete(one.id, captured, now, now + timedelta(days=1))
        assert not await journal.complete(one.id, CachedResponse(status_code=204, body=b""), now, first.expires_at)
    async with db.sessions.begin() as session:
        replay = await requests(session, db).reserve(replace(first, id=uuid4()), now)
        assert replay.id == one.id
        assert replay.response == captured
        assert replay.expires_at >= before + timedelta(days=1)
        assert (
            await session.execute(select(func.count()).select_from(db.tables["request_generations"]))
        ).scalar_one() == 1
        generations = db.tables["request_generations"]
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(generations).where(generations.c.id == one.id).values(response_body=body + b" ")
                )


async def test_expiry_keeps_generation_and_binding_but_fences_old_completion(database: Database) -> None:
    db = database
    generations = db.tables["request_generations"]
    async with db.sessions.begin() as session:
        now = await database_now(session)
        candidate = operation(now)
        await SqlOperationJournal(session, db.tables["operations"]).reserve(candidate)
        old = replace(request(now, db.actor), created_at=now - timedelta(days=2))
        await requests(session, db).reserve(old, now)
        assert await bindings(session, db).bind(operation_id=candidate.id, request_id=old.id)
    async with db.sessions.begin() as session:
        # Explicit fixture aging avoids a 24h wait; retention CHECK still holds.
        await session.execute(
            update(generations).where(generations.c.id == old.id).values(expires_at=now - timedelta(seconds=1))
        )
    async with db.sessions.begin() as session:
        current = request(await database_now(session), db.actor)
        assert await requests(session, db).reserve(current, current.created_at) == current
        assert not await requests(session, db).complete(
            old.id, CachedResponse(status_code=204, body=b""), now, old.expires_at
        )
        assert await bindings(session, db).get_operation_id(old.id) == candidate.id
        assert (await session.execute(select(func.count()).select_from(generations))).scalar_one() == 2
    async with db.sessions.begin() as session:
        # An identical old binding remains readable after expiry/replacement.
        assert not await bindings(session, db).bind(operation_id=candidate.id, request_id=old.id)
        assert await bindings(session, db).bind(operation_id=candidate.id, request_id=current.id)
        assert await requests(session, db).complete(
            current.id, CachedResponse(status_code=204, body=b""), now, current.expires_at
        )


async def test_skip_locked_claim_and_stale_worker_cannot_overwrite_reclaimed_work(database: Database) -> None:
    db = database
    table = db.tables["operations"]
    policy = RetryPolicy()
    async with db.sessions.begin() as session:
        now = await database_now(session)
        first = operation(now - timedelta(seconds=2), "first")
        second = operation(now - timedelta(seconds=1), "second")
        journal = SqlOperationJournal(session, table)
        await journal.reserve(first)
        await journal.reserve(second)
    async with db.sessions.begin() as holder:
        claimed = await SqlOperationJournal(holder, table).claim_next(now=now, owner="first-worker", policy=policy)
        assert claimed is not None
        assert claimed.id == first.id
        assert claimed.work.lease is not None
        async with db.sessions.begin() as other:
            skipped = await SqlOperationJournal(other, table).claim_next(now=now, owner="second-worker", policy=policy)
            assert skipped is not None
            assert skipped.id == second.id
    async with db.sessions.begin() as session:
        await session.execute(
            update(table).where(table.c.id == first.id).values(lease_until=now - timedelta(seconds=1))
        )
    async with db.sessions.begin() as session:
        newer = await SqlOperationJournal(session, table).claim_next(now=now, owner="replacement", policy=policy)
        assert newer is not None
        assert newer.id == claimed.id
        assert newer.work.lease is not None
        assert newer.work.lease.token != claimed.work.lease.token
    async with db.sessions.begin() as session:
        stale = replace(claimed, work=claimed.work.succeed(token=claimed.work.lease.token, now=now))
        assert not await SqlOperationJournal(session, table).save_leased(stale, token=claimed.work.lease.token, now=now)
        finished = replace(
            newer, work=newer.work.succeed(token=newer.work.lease.token, now=now), external_id="project:1"
        )
        assert await SqlOperationJournal(session, table).save_leased(finished, token=newer.work.lease.token, now=now)
        await session.rollback()
    async with db.sessions() as session:
        assert (
            await session.execute(select(table.c.state).where(table.c.id == first.id))
        ).scalar_one() == WorkState.RUNNING


async def test_post_lock_database_time_rejects_expiry_and_quarantine_survives_empty_claim(database: Database) -> None:
    db = database
    table = db.tables["operations"]
    policy = RetryPolicy(max_attempts=1)
    async with db.sessions.begin() as session:
        now = await database_now(session)
        candidate = operation(now - timedelta(days=1))
        journal = SqlOperationJournal(session, table)
        await journal.reserve(candidate)
        claimed = await journal.claim_next(now=now, owner="worker", policy=policy)
        assert claimed is not None
        assert claimed.work.lease is not None
    token = claimed.work.lease.token
    attempted = replace(claimed, work=claimed.work.succeed(token=token, now=now))
    async with db.sessions() as holder, db.sessions() as saver:
        await holder.execute(select(table.c.id).where(table.c.id == claimed.id).with_for_update())
        task = asyncio.create_task(
            SqlOperationJournal(saver, table).save_leased(attempted, token=token, now=candidate.work.created_at)
        )
        try:
            await asyncio.sleep(0)
            await holder.execute(
                update(table).where(table.c.id == claimed.id).values(lease_until=now - timedelta(seconds=1))
            )
            await holder.commit()
            assert not await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with db.sessions.begin() as session:
        assert await SqlOperationJournal(session, table).claim_next(now=now, owner="replacement", policy=policy) is None
    async with db.sessions() as session:
        row = (await session.execute(select(table).where(table.c.id == claimed.id))).mappings().one()
        assert row["state"] == WorkState.NEEDS_ATTENTION
        assert row["last_error"] == FailureCode.EXHAUSTED
        assert row["lease_token"] is None
        assert row["external_id"] is None


async def test_begin_replays_later_contender_after_reservation_barrier(database: Database) -> None:
    db = database
    paused, resume = asyncio.Event(), asyncio.Event()
    candidates: list[RequestRecord] = []

    class PausedJournal(SqlRequestJournal):
        @override
        async def reserve(self, candidate: RequestRecord, now: datetime) -> RequestRecord:
            candidates.append(candidate)
            paused.set()
            await resume.wait()
            return await super().reserve(candidate, now)

    scope = RequestScope(actor_id=db.actor, method="POST", path="/assignments", key="contended")
    policy = ResponseCapturePolicy(
        method=scope.method, path=scope.path, allowed_statuses=frozenset({204}), allow_empty_body=True
    )
    generations = db.tables["request_generations"]
    async with db.sessions.begin() as first_session:
        first_clock = ControlledClock(value=await database_now(first_session) - timedelta(seconds=1))
        first = RequestIdempotency(clock=first_clock, response_policies=(policy,))
        journal = PausedJournal(
            first_session,
            request_scopes_table=db.tables["request_scopes"],
            request_generations_table=generations,
        )
        task = asyncio.create_task(first.begin(journal, scope, b"{}"))
        try:
            await asyncio.wait_for(paused.wait(), timeout=5)
            async with db.sessions.begin() as second_session:
                second_clock = ControlledClock(value=await database_now(second_session))
                second = RequestIdempotency(clock=second_clock, response_policies=(policy,))
                second_journal = requests(second_session, db)
                winner = await second.begin(second_journal, scope, b"{}")
                assert isinstance(winner, RequestRecord)
                assert len(candidates) == 1
                assert candidates[0].created_at < winner.created_at
                assert candidates[0].id != winner.id
                assert await second.complete(second_journal, winner, status_code=204, body=b"")
                retained = (await second_session.execute(select(generations))).mappings().one()
            first_clock.value = await database_now(first_session)
            assert first_clock.value > winner.created_at
            resume.set()
            assert await asyncio.wait_for(task, timeout=5) == CachedResponse(status_code=204, body=b"")
            # A returns B's committed outcome without rewriting B's identity/times.
            assert (await first_session.execute(select(generations))).mappings().all() == [retained]
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
