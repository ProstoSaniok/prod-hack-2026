"""Persistence contracts for future adapters; none of these methods commit.

Request/finalization callers supply their existing transaction. Recovery workers
use fresh factory scopes, commit claims, close them, then perform network I/O.
Exiting any scope without commit must roll back. No live persistence is wired.
"""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.application.operations.entity import Operation, OutboxMessage, ProjectionSpec
from src.application.operations.work import RetryPolicy


class OperationJournal(Protocol):
    async def reserve(self, candidate: Operation) -> Operation:
        """Atomically insert or return permanent unique (kind, source_key)."""
        ...

    async def claim_next(self, *, now: datetime, owner: str, policy: RetryPolicy) -> Operation | None:
        """Atomically claim due work using Work.claim and a fresh UUID fence.

        Quarantine exhausted expired claims, then skip them. Order by due time/id;
        use SKIP LOCKED or equivalent. Claims become durable only at caller commit.
        """
        ...

    async def save_leased(self, operation: Operation, *, token: UUID, now: datetime) -> bool:
        """CAS a running row with matching token AND lease_until > now.

        Do not insert missing rows. False means ownership was lost. The caller's
        commit atomically persists the external ID and final state.
        """
        ...


class Outbox(Protocol):
    async def enqueue(self, candidate: OutboxMessage) -> OutboxMessage:
        """Insert or return permanent unique (topic, source_key), in caller UoW."""
        ...

    async def claim_next(self, *, now: datetime, owner: str, policy: RetryPolicy) -> OutboxMessage | None:
        """Same atomic/fenced claim contract as OperationJournal.claim_next."""
        ...

    async def save_leased(self, message: OutboxMessage, *, token: UUID, now: datetime) -> bool:
        """Same compare-and-set contract as OperationJournal.save_leased."""
        ...


class OperationTransaction(Protocol):
    @property
    def operations(self) -> OperationJournal: ...

    @property
    def outbox(self) -> Outbox: ...

    async def finalize(self, operation: Operation) -> None:
        """Apply REQUIRED operation-specific domain effects in this same UoW.

        Record the resource/replay outcome and append appropriate outbox events.
        No remote I/O or nested commits. No default/no-op production finalizer:
        missing real collaborators must block adapter registration.
        """
        ...

    async def commit(self) -> None: ...


class OperationTransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[OperationTransaction]:
        """Open a NEW session/UoW, never reuse a request or another worker's scope."""
        ...


class ProjectionReceipts(Protocol):
    async def reserve(self, *, consumer: str, spec: ProjectionSpec) -> bool:
        """Unique (consumer, topic, source_key), validating subject/revision.

        Return False for exact repeats, conflict for different inputs. Reservation
        and projection effects must commit or roll back together, even on crash.
        """
        ...


class ProjectionTransaction(Protocol):
    @property
    def receipts(self) -> ProjectionReceipts: ...

    async def apply(self, spec: ProjectionSpec) -> None:
        """Apply LOCAL DB effects; no commit or remote I/O.

        Append immutable revisions or conditionally replace only older revisions;
        an old delayed event must not overwrite newer published content.
        """
        ...

    async def commit(self) -> None: ...


class ProjectionTransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[ProjectionTransaction]: ...
