"""Test-only transactional collaborators. Never imported by application code."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.application.operations.entity import Operation, OutboxMessage, ProjectionSpec, SourceConflictError
from src.application.operations.ports import (
    OperationJournal,
    OperationTransaction,
    Outbox,
    ProjectionReceipts,
    ProjectionTransaction,
)
from src.application.operations.work import LeaseLostError, RetryPolicy, WorkState


class FakeClock:
    def __init__(self) -> None:
        self.instant = datetime(2026, 10, 16, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


class MemoryDatabase:
    def __init__(self) -> None:
        self.operations: dict[UUID, Operation] = {}
        self.messages: dict[UUID, OutboxMessage] = {}
        self.finalized: set[UUID] = set()
        self.receipts: dict[tuple[str, str, str], ProjectionSpec] = {}
        self.projections: dict[tuple[str, UUID], int] = {}
        self.lock = asyncio.Lock()
        self.active_scopes = 0
        self.commits = 0
        self.fail_commit_at: int | None = None
        self.fail_after_commit = False
        self.fail_apply = False
        self.fail_finalize = False
        self.applied = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[OperationTransaction]:
        async with self.lock:
            self.active_scopes += 1
            try:
                yield MemoryTransaction(self)
            finally:
                self.active_scopes -= 1

    @asynccontextmanager
    async def projection_transaction(self) -> AsyncIterator[ProjectionTransaction]:
        async with self.lock:
            self.active_scopes += 1
            try:
                yield MemoryProjectionTransaction(self)
            finally:
                self.active_scopes -= 1


class MemoryOperations:
    def __init__(self, rows: dict[UUID, Operation]) -> None:
        self.rows = rows

    async def reserve(self, candidate: Operation) -> Operation:
        for row in self.rows.values():
            if (row.spec.kind, row.spec.source_key) == (candidate.spec.kind, candidate.spec.source_key):
                return row
        self.rows[candidate.id] = candidate
        return candidate

    async def claim_next(self, *, now: datetime, owner: str, policy: RetryPolicy) -> Operation | None:
        for row in sorted(self.rows.values(), key=lambda row: (row.work.available_at, row.id)):
            if row.work.is_due(now):
                claimed = replace(row, work=row.work.claim(now=now, owner=owner, token=uuid4(), policy=policy))
                self.rows[row.id] = claimed
                if claimed.work.state == WorkState.RUNNING:
                    return claimed
        return None

    async def save_leased(self, operation: Operation, *, token: UUID, now: datetime) -> bool:
        current = self.rows.get(operation.id)
        if current is None:
            return False
        try:
            current.work.require_lease(token=token, now=now)
        except LeaseLostError:
            return False
        self.rows[operation.id] = operation
        return True


class MemoryOutbox:
    def __init__(self, rows: dict[UUID, OutboxMessage]) -> None:
        self.rows = rows

    async def enqueue(self, candidate: OutboxMessage) -> OutboxMessage:
        for row in self.rows.values():
            if (row.spec.topic, row.spec.source_key) == (candidate.spec.topic, candidate.spec.source_key):
                return row
        self.rows[candidate.id] = candidate
        return candidate

    async def claim_next(self, *, now: datetime, owner: str, policy: RetryPolicy) -> OutboxMessage | None:
        for row in sorted(self.rows.values(), key=lambda row: (row.work.available_at, row.id)):
            if row.work.is_due(now):
                claimed = replace(row, work=row.work.claim(now=now, owner=owner, token=uuid4(), policy=policy))
                self.rows[row.id] = claimed
                if claimed.work.state == WorkState.RUNNING:
                    return claimed
        return None

    async def save_leased(self, message: OutboxMessage, *, token: UUID, now: datetime) -> bool:
        current = self.rows.get(message.id)
        if current is None:
            return False
        try:
            current.work.require_lease(token=token, now=now)
        except LeaseLostError:
            return False
        self.rows[message.id] = message
        return True


class MemoryTransaction:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        self.operation_rows = database.operations.copy()
        self.message_rows = database.messages.copy()
        self.finalized = database.finalized.copy()

    @property
    def operations(self) -> OperationJournal:
        return MemoryOperations(self.operation_rows)

    @property
    def outbox(self) -> Outbox:
        return MemoryOutbox(self.message_rows)

    async def finalize(self, operation: Operation) -> None:
        if self.database.fail_finalize:
            raise RuntimeError("Simulated domain finalization failure")
        self.finalized.add(operation.id)

    async def commit(self) -> None:
        self.database.commits += 1
        fail = self.database.commits == self.database.fail_commit_at
        if fail and not self.database.fail_after_commit:
            raise RuntimeError("Simulated failure before commit")
        self.database.operations = self.operation_rows.copy()
        self.database.messages = self.message_rows.copy()
        self.database.finalized = self.finalized.copy()
        if fail:
            raise RuntimeError("Simulated lost commit acknowledgement")


class MemoryReceipts:
    def __init__(self, rows: dict[tuple[str, str, str], ProjectionSpec]) -> None:
        self.rows = rows

    async def reserve(self, *, consumer: str, spec: ProjectionSpec) -> bool:
        key = (consumer, spec.topic, spec.source_key)
        existing = self.rows.get(key)
        if existing is not None:
            if existing != spec:
                raise SourceConflictError("Conflicting projection source")
            return False
        self.rows[key] = spec
        return True


class MemoryProjectionTransaction:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        self.receipt_rows = database.receipts.copy()
        self.projections = database.projections.copy()
        self.applied = 0

    @property
    def receipts(self) -> ProjectionReceipts:
        return MemoryReceipts(self.receipt_rows)

    async def apply(self, spec: ProjectionSpec) -> None:
        if self.database.fail_apply:
            raise RuntimeError("Simulated projection failure")
        key = (spec.topic, spec.subject_id)
        if self.projections.get(key, 0) < spec.revision:
            self.projections[key] = spec.revision
            self.applied += 1

    async def commit(self) -> None:
        self.database.receipts = self.receipt_rows.copy()
        self.database.projections = self.projections.copy()
        self.database.applied += self.applied
