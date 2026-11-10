import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from src.application.operations.entity import Operation, OperationSpec, SourceConflictError
from src.application.operations.recovery import (
    DependencyError,
    Lookup,
    LookupState,
    OperationReservations,
    RecoveryRunner,
    RunResult,
)
from src.application.operations.work import FailureCode, RetryPolicy, WorkState
from tests.unit.operations.fakes import FakeClock, MemoryDatabase


def spec() -> OperationSpec:
    return OperationSpec(kind="repository.ensure", source_key="project:42", subject_id=uuid4(), payload_hash="a" * 64)


class Effect:
    def __init__(self, database: MemoryDatabase, *, remote: dict[str, str] | None = None) -> None:
        self.database = database
        self.remote = remote if remote is not None else {}
        self.creates = 0
        self.lookups = 0
        self.lookup_state = LookupState.ABSENT
        self.lookup_result: Lookup | None = None
        self.error: Exception | None = None
        self.lose_response = False
        self.entered = asyncio.Event()
        self.wait: asyncio.Event | None = None

    async def lookup(self, operation: Operation) -> Lookup:
        assert self.database.active_scopes == 0
        assert operation.id in self.database.operations
        assert self.database.operations[operation.id].work.state == WorkState.RUNNING
        self.lookups += 1
        if self.lookup_result is not None:
            return self.lookup_result
        external_id = self.remote.get(operation.spec.source_key)
        if external_id is not None:
            return Lookup(state=LookupState.FOUND, external_id=external_id)
        return Lookup(state=self.lookup_state)

    async def ensure(self, operation: Operation) -> str:
        assert self.database.active_scopes == 0
        self.entered.set()
        if self.wait is not None:
            await self.wait.wait()
        if self.error is not None:
            raise self.error
        if operation.spec.source_key not in self.remote:
            self.creates += 1
            self.remote[operation.spec.source_key] = "repo-42"
        if self.lose_response:
            raise TimeoutError("upstream body with secret must never be stored")
        return self.remote[operation.spec.source_key]


async def setup_operation(database: MemoryDatabase, clock: FakeClock) -> Operation:
    async with database.transaction() as transaction:
        operation = await OperationReservations(clock).reserve(transaction.operations, spec())
        await transaction.commit()
    return operation


def runner(
    database: MemoryDatabase, clock: FakeClock, effect: Effect, *, policy: RetryPolicy | None = None
) -> RecoveryRunner:
    return RecoveryRunner(transactions=database.transaction, effect=effect, clock=clock, owner="t03", policy=policy)


async def test_reservation_is_permanent_caller_transactional_and_conflicts() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    clock.advance(timedelta(days=3))
    async with database.transaction() as transaction:
        replay = await OperationReservations(clock).reserve(transaction.operations, operation.spec)
        assert replay == operation
        with pytest.raises(SourceConflictError):
            await OperationReservations(clock).reserve(
                transaction.operations, replace(operation.spec, payload_hash="b" * 64)
            )
    async with database.transaction() as transaction:
        await OperationReservations(clock).reserve(transaction.operations, replace(spec(), source_key="project:43"))
    assert len(database.operations) == 1


async def test_timeout_after_success_reconciles_without_second_create() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    effect.lose_response = True
    worker = runner(database, clock, effect)
    assert await worker.run_once() == RunResult.RETRY_WAIT
    assert database.operations[operation.id].work.last_error == FailureCode.TIMEOUT
    assert await worker.run_once() == RunResult.IDLE
    clock.advance(timedelta(seconds=1))
    assert await worker.run_once() == RunResult.SUCCEEDED
    assert effect.creates == 1
    assert effect.lookups == 2
    assert database.operations[operation.id].external_id == "repo-42"
    assert database.finalized == {operation.id}


async def test_abandoned_claim_is_reclaimed_after_restart() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    async with database.transaction() as transaction:
        claim = await transaction.operations.claim_next(now=clock.now(), owner="dead", policy=RetryPolicy())
        await transaction.commit()
    assert claim is not None
    effect = Effect(database)
    worker = runner(database, clock, effect)
    assert await worker.run_once() == RunResult.IDLE
    clock.advance(timedelta(seconds=30))
    assert await worker.run_once() == RunResult.SUCCEEDED
    assert database.operations[operation.id].work.attempts == 2
    assert effect.creates == 1


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_external_success_then_database_failure_recovered(*, lost_ack: bool) -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    database.fail_commit_at = database.commits + 2
    database.fail_after_commit = lost_ack
    with pytest.raises(RuntimeError, match="Simulated"):
        await runner(database, clock, effect).run_once()
    clock.advance(timedelta(seconds=30))
    result = await runner(database, clock, effect).run_once()
    assert result == (RunResult.IDLE if lost_ack else RunResult.SUCCEEDED)
    assert effect.creates == 1
    assert database.operations[operation.id].work.state == WorkState.SUCCEEDED
    assert database.finalized == {operation.id}


async def test_domain_finalization_failure_rolls_back_and_reconciles() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    database.fail_finalize = True
    with pytest.raises(RuntimeError, match="domain finalization failure"):
        await runner(database, clock, effect).run_once()
    assert database.operations[operation.id].work.state == WorkState.RUNNING
    assert not database.finalized
    database.fail_finalize = False
    clock.advance(timedelta(seconds=30))
    assert await runner(database, clock, effect).run_once() == RunResult.SUCCEEDED
    assert effect.creates == 1
    assert database.finalized == {operation.id}


async def test_concurrent_workers_claim_only_once() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    await setup_operation(database, clock)
    effect = Effect(database)
    results = await asyncio.gather(
        runner(database, clock, effect).run_once(), runner(database, clock, effect).run_once()
    )
    assert sorted(results) == sorted([RunResult.SUCCEEDED, RunResult.IDLE])
    assert effect.creates == 1


@pytest.mark.parametrize(
    ("lookup_state", "expected"),
    [(LookupState.UNCERTAIN, RunResult.RETRY_WAIT), (LookupState.CONFLICT, RunResult.NEEDS_ATTENTION)],
)
async def test_unproven_absence_never_mutates(lookup_state: LookupState, expected: RunResult) -> None:
    database, clock = MemoryDatabase(), FakeClock()
    await setup_operation(database, clock)
    effect = Effect(database)
    effect.lookup_state = lookup_state
    assert await runner(database, clock, effect).run_once() == expected
    assert effect.creates == 0


async def test_unknown_adapter_lookup_result_never_mutates() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    # Deliberately violate the port contract to exercise the defensive mutation
    # boundary, even if an adapter bypasses Lookup's constructor validation.
    effect.lookup_result = cast(Lookup, SimpleNamespace(state="unexpected", external_id=None))
    assert await runner(database, clock, effect).run_once() == RunResult.RETRY_WAIT
    assert effect.creates == 0
    assert database.operations[operation.id].work.last_error == FailureCode.UNCERTAIN


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (RuntimeError("password=secret"), RunResult.NEEDS_ATTENTION, FailureCode.INTERNAL),
        (DependencyError(FailureCode.UNAVAILABLE), RunResult.RETRY_WAIT, FailureCode.UNAVAILABLE),
    ],
)
async def test_failures_are_sanitized(error: Exception, expected: RunResult, code: FailureCode) -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    effect.error = error
    assert await runner(database, clock, effect).run_once() == expected
    assert database.operations[operation.id].work.last_error == code
    assert "password" not in repr(database.operations[operation.id])


async def test_cancellation_keeps_durable_claim_for_later_reconciliation() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    effect.wait = asyncio.Event()
    task = asyncio.create_task(runner(database, clock, effect).run_once())
    await effect.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert database.operations[operation.id].work.state == WorkState.RUNNING
    assert database.active_scopes == 0


async def test_actual_io_timeout_is_bounded_and_scheduled() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    effect.wait = asyncio.Event()
    policy = RetryPolicy(io_timeout=timedelta(milliseconds=1))
    assert await runner(database, clock, effect, policy=policy).run_once() == RunResult.RETRY_WAIT
    assert database.operations[operation.id].work.last_error == FailureCode.TIMEOUT
    assert database.active_scopes == 0


async def test_expired_worker_cannot_finalize_after_new_owner() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    effect = Effect(database)
    effect.wait = asyncio.Event()
    old = asyncio.create_task(runner(database, clock, effect).run_once())
    await effect.entered.wait()
    clock.advance(timedelta(seconds=30))
    new_effect = Effect(database, remote=effect.remote)
    assert await runner(database, clock, new_effect).run_once() == RunResult.SUCCEEDED
    effect.wait.set()
    assert await old == RunResult.LEASE_LOST
    assert database.operations[operation.id].work.state == WorkState.SUCCEEDED
    assert effect.creates + new_effect.creates == 1


async def test_last_crashed_attempt_quarantines_not_recreates() -> None:
    database, clock = MemoryDatabase(), FakeClock()
    operation = await setup_operation(database, clock)
    policy = RetryPolicy(max_attempts=1)
    async with database.transaction() as transaction:
        await transaction.operations.claim_next(now=clock.now(), owner="dead", policy=policy)
        await transaction.commit()
    clock.advance(policy.lease_for)
    effect = Effect(database)
    assert await runner(database, clock, effect, policy=policy).run_once() == RunResult.IDLE
    assert database.operations[operation.id].work.state == WorkState.NEEDS_ATTENTION
    assert effect.creates == 0
