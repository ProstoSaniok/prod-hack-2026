"""Explicitly owned worker lifecycle; the application awaits shutdown."""

from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from types import TracebackType
from typing import Protocol

from src.application.operations.recovery import RunResult


class Runner(Protocol):
    async def run_once(self) -> RunResult: ...


class ManagedWorker:
    def __init__(self, runner: Runner, *, idle_seconds: float = 1, shutdown_seconds: float = 25) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (idle_seconds, shutdown_seconds)):
            raise ValueError("Worker waits must be positive and finite")
        self.runner = runner
        self.idle_seconds = idle_seconds
        self.shutdown_seconds = shutdown_seconds
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.closed = False

    async def __aenter__(self) -> ManagedWorker:
        self.start()
        return self

    def start(self) -> None:
        if self.task is not None or self.stop_event.is_set():
            raise RuntimeError("Worker lifecycle already entered")
        self.task = asyncio.create_task(self.run(), name="prodguild-operation-worker")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            result = await self.runner.run_once()
            if result == RunResult.IDLE:
                try:
                    async with asyncio.timeout(self.idle_seconds):
                        await self.stop_event.wait()
                except TimeoutError:
                    continue

    async def close(self) -> None:
        if self.closed:
            return
        self.stop_event.set()
        if self.task is None:
            self.closed = True
            return
        try:
            async with asyncio.timeout(self.shutdown_seconds):
                await asyncio.shield(self.task)
        except TimeoutError:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        except asyncio.CancelledError:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            raise
        finally:
            self.closed = True
