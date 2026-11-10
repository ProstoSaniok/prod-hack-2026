import asyncio

import pytest

from src.adapters.operations.worker import ManagedWorker
from src.application.operations.recovery import RunResult


class IdleRunner:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.calls = 0

    async def run_once(self) -> RunResult:
        self.calls += 1
        self.entered.set()
        return RunResult.IDLE


class BlockingRunner:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.calls = 0

    async def run_once(self) -> RunResult:
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return RunResult.SUCCEEDED


async def test_idle_worker_wakes_immediately_for_shutdown_and_joins() -> None:
    runner = IdleRunner()
    async with ManagedWorker(runner, idle_seconds=3600) as worker:
        await runner.entered.wait()
    assert worker.task is not None
    assert worker.task.done()
    assert runner.calls == 1
    await worker.close()
    with pytest.raises(RuntimeError, match="already entered"):
        worker.start()


async def test_graceful_shutdown_finishes_inflight_without_new_claims() -> None:
    runner = BlockingRunner()
    worker = ManagedWorker(runner)
    worker.start()
    await runner.entered.wait()
    closing = asyncio.create_task(worker.close())
    await asyncio.sleep(0)
    runner.release.set()
    await closing
    assert not runner.cancelled
    assert runner.calls == 1
    assert worker.task is not None
    assert worker.task.done()


async def test_shutdown_timeout_cancels_and_joins_inflight() -> None:
    runner = BlockingRunner()
    async with ManagedWorker(runner, shutdown_seconds=0.001) as worker:
        await runner.entered.wait()
    assert runner.cancelled
    assert worker.task is not None
    assert worker.task.done()
    await worker.close()


async def test_cancellation_of_close_also_joins_owned_task() -> None:
    runner = BlockingRunner()
    worker = ManagedWorker(runner)
    worker.start()
    await runner.entered.wait()
    closing = asyncio.create_task(worker.close())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runner.cancelled
    assert worker.task is not None
    assert worker.task.done()


async def test_worker_failure_is_observed_by_owner() -> None:
    entered = asyncio.Event()

    class BrokenRunner:
        async def run_once(self) -> RunResult:
            entered.set()
            raise RuntimeError("database unavailable")

    worker = ManagedWorker(BrokenRunner())
    worker.start()
    await entered.wait()
    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.close()


async def test_unused_close_and_invalid_waits() -> None:
    await ManagedWorker(IdleRunner()).close()
    with pytest.raises(ValueError, match="positive"):
        ManagedWorker(IdleRunner(), idle_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        ManagedWorker(IdleRunner(), shutdown_seconds=0)
