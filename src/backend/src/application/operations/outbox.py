"""At-least-once dispatch and transactional local projection deduplication."""

import asyncio
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from src.application.operations.clock import Clock
from src.application.operations.entity import OutboxMessage, ProjectionSpec, SourceConflictError, validate_name
from src.application.operations.ports import OperationTransactionFactory, Outbox, ProjectionTransactionFactory
from src.application.operations.recovery import DependencyError, RunResult, result_for
from src.application.operations.work import FailureCode, LeaseLostError, RetryPolicy, Work


class OutboxWriter:
    """Stages only; T09 can share its UoW with wallet/development collaborators."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def enqueue(self, outbox: Outbox, spec: ProjectionSpec) -> OutboxMessage:
        now = self.clock.now()
        candidate = OutboxMessage(id=uuid4(), spec=spec, work=Work(created_at=now, available_at=now))
        message = await outbox.enqueue(candidate)
        if message.spec != spec:
            raise SourceConflictError("Permanent outbox source has different inputs")
        return message


class ProjectionHandler(Protocol):
    async def deliver(self, message: OutboxMessage) -> None:
        """Must be idempotent by permanent source, with revision-aware ordering.

        Use TransactionalProjection for local database effects. Remote projections
        require deterministic lookup/ensure guarantees, NOT a receipt committed
        before network I/O. Never expose pending/private review content.
        """
        ...


class TransactionalProjection:
    """Atomically apply local projection effects with their permanent receipt."""

    def __init__(self, *, consumer: str, transactions: ProjectionTransactionFactory) -> None:
        validate_name(consumer)
        self.consumer = consumer
        self.transactions = transactions

    async def deliver(self, message: OutboxMessage) -> None:
        async with self.transactions() as transaction:
            if await transaction.receipts.reserve(consumer=self.consumer, spec=message.spec):
                # apply must update only older revisions (or append immutable data).
                await transaction.apply(message.spec)
            await transaction.commit()


class OutboxDispatcher:
    def __init__(
        self,
        *,
        transactions: OperationTransactionFactory,
        handler: ProjectionHandler,
        clock: Clock,
        owner: str,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.transactions = transactions
        self.handler = handler
        self.clock = clock
        self.owner = owner
        self.policy = policy if policy is not None else RetryPolicy()

    async def run_once(self) -> RunResult:
        async with self.transactions() as transaction:
            message = await transaction.outbox.claim_next(now=self.clock.now(), owner=self.owner, policy=self.policy)
            await transaction.commit()
        if message is None:
            return RunResult.IDLE
        lease = message.work.lease
        if lease is None:
            raise ValueError("Outbox returned an unclaimed message")
        error: FailureCode | None = None
        try:
            async with asyncio.timeout(self.policy.io_timeout.total_seconds()):
                await self.handler.deliver(message)
        except DependencyError as failure:
            error = FailureCode(failure.code)
        except TimeoutError:
            error = FailureCode.TIMEOUT
        except Exception:
            error = FailureCode.INTERNAL
        now = self.clock.now()
        try:
            work = (
                message.work.succeed(token=lease.token, now=now)
                if error is None
                else message.work.fail(token=lease.token, now=now, code=error, policy=self.policy)
            )
        except LeaseLostError:
            return RunResult.LEASE_LOST
        updated = replace(message, work=work)
        async with self.transactions() as transaction:
            if not await transaction.outbox.save_leased(updated, token=lease.token, now=self.clock.now()):
                return RunResult.LEASE_LOST
            await transaction.commit()
        return result_for(work)
