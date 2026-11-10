import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from src.application.operations.entity import OutboxMessage, ProjectionSpec, SourceConflictError
from src.application.operations.outbox import OutboxDispatcher, OutboxWriter, TransactionalProjection
from src.application.operations.recovery import DependencyError, RunResult
from src.application.operations.work import FailureCode, RetryPolicy, WorkState
from tests.unit.operations.fakes import FakeClock, MemoryDatabase


def spec() -> ProjectionSpec:
    return ProjectionSpec(topic="review.published", source_key="review:42:revision:1", subject_id=uuid4(), revision=1)


async def enqueue(database: MemoryDatabase, clock: FakeClock) -> OutboxMessage:
    async with database.transaction() as transaction:
        message = await OutboxWriter(clock).enqueue(transaction.outbox, spec())
        await transaction.commit()
    return message


def consumer(database: MemoryDatabase) -> TransactionalProjection:
    return TransactionalProjection(consumer="activity", transactions=database.projection_transaction)


def dispatcher(database: MemoryDatabase, clock: FakeClock) -> OutboxDispatcher:
    return OutboxDispatcher(
        transactions=database.transaction, handler=consumer(database), clock=clock, owner="t03-outbox"
    )


async def test_outbox_is_caller_owned_and_permanent_deduplication_conflicts() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    clock.advance(timedelta(days=4))
    async with database.transaction() as transaction:
        assert await OutboxWriter(clock).enqueue(transaction.outbox, message.spec) == message
        with pytest.raises(SourceConflictError):
            await OutboxWriter(clock).enqueue(transaction.outbox, replace(message.spec, revision=2))
    async with database.transaction() as transaction:
        await OutboxWriter(clock).enqueue(transaction.outbox, replace(spec(), source_key="review:43:revision:1"))
    assert len(database.messages) == 1


async def test_duplicate_delivery_and_concurrent_consumers_apply_once() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    projection = consumer(database)
    await asyncio.gather(projection.deliver(message), projection.deliver(message))
    await projection.deliver(message)
    assert database.applied == 1
    assert len(database.receipts) == 1


async def test_receipt_and_effect_roll_back_together() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    database.fail_apply = True
    with pytest.raises(RuntimeError, match="projection failure"):
        await consumer(database).deliver(message)
    assert not database.receipts
    assert not database.projections
    database.fail_apply = False
    await consumer(database).deliver(message)
    assert database.applied == 1


async def test_receipt_conflicts_and_old_revision_cannot_overwrite_new() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    projection = consumer(database)
    newer = replace(message, spec=replace(message.spec, revision=2, source_key="review:42:revision:2"))
    await projection.deliver(newer)
    await projection.deliver(message)
    assert database.projections[(message.spec.topic, message.spec.subject_id)] == 2
    assert database.applied == 1
    with pytest.raises(SourceConflictError):
        await projection.deliver(replace(message, spec=replace(message.spec, revision=3)))


async def test_crash_after_projection_before_ack_redelivers_without_duplicate_effect() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    database.fail_commit_at = database.commits + 2
    with pytest.raises(RuntimeError, match="before commit"):
        await dispatcher(database, clock).run_once()
    assert database.applied == 1
    assert database.messages[message.id].work.state == WorkState.RUNNING
    clock.advance(timedelta(seconds=30))
    assert await dispatcher(database, clock).run_once() == RunResult.SUCCEEDED
    assert database.applied == 1
    assert database.messages[message.id].work.attempts == 2


async def test_concurrent_dispatch_and_exhaustion() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    results = await asyncio.gather(dispatcher(database, clock).run_once(), dispatcher(database, clock).run_once())
    assert sorted(results) == sorted([RunResult.SUCCEEDED, RunResult.IDLE])
    assert database.applied == 1
    assert database.messages[message.id].work.state == WorkState.SUCCEEDED


async def test_expired_outbox_worker_cannot_ack_new_claim_and_receipt_deduplicates() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedProjection:
        async def deliver(self, pending: OutboxMessage) -> None:
            entered.set()
            await release.wait()
            await consumer(database).deliver(pending)

    old_worker = OutboxDispatcher(
        transactions=database.transaction, handler=DelayedProjection(), clock=clock, owner="old"
    )
    task = asyncio.create_task(old_worker.run_once())
    await entered.wait()
    clock.advance(timedelta(seconds=30))
    assert await dispatcher(database, clock).run_once() == RunResult.SUCCEEDED
    release.set()
    assert await task == RunResult.LEASE_LOST
    assert database.messages[message.id].work.state == WorkState.SUCCEEDED
    assert database.applied == 1


class FailingProjection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def deliver(self, message: OutboxMessage) -> None:
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (TimeoutError("private upstream response"), RunResult.RETRY_WAIT, FailureCode.TIMEOUT),
        (DependencyError(FailureCode.UNAVAILABLE), RunResult.RETRY_WAIT, FailureCode.UNAVAILABLE),
        (RuntimeError("password=private"), RunResult.NEEDS_ATTENTION, FailureCode.INTERNAL),
    ],
)
async def test_delivery_errors_are_bounded_and_sanitized(
    error: Exception, expected: RunResult, code: FailureCode
) -> None:
    database, clock = MemoryDatabase(), FakeClock()
    message = await enqueue(database, clock)
    worker = OutboxDispatcher(
        transactions=database.transaction,
        handler=FailingProjection(error),
        clock=clock,
        owner="outbox",
        policy=RetryPolicy(max_attempts=2),
    )
    assert await worker.run_once() == expected
    assert database.messages[message.id].work.last_error == code
    assert "private" not in repr(database.messages[message.id])
    clock.advance(timedelta(seconds=1))
    assert await worker.run_once() == (RunResult.IDLE if code == FailureCode.INTERNAL else RunResult.NEEDS_ATTENTION)
    assert database.messages[message.id].work.state == WorkState.NEEDS_ATTENTION
