"""Reconcile durable intentions before repeating any external mutation."""

import asyncio
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from src.application.common.errors import DependencyUnavailableError
from src.application.operations.clock import Clock
from src.application.operations.entity import Operation, OperationSpec, SourceConflictError
from src.application.operations.ports import OperationJournal, OperationTransactionFactory
from src.application.operations.work import FailureCode, LeaseLostError, RetryPolicy, Work, WorkState


class LookupState(StrEnum):
    FOUND = "found"
    ABSENT = "absent"
    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"


def validate_external_id(value: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is None:
        raise ValueError("Expected a stable external ID, not a URL or response body")


@dataclass(frozen=True, kw_only=True)
class Lookup:
    state: LookupState
    external_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, LookupState):
            raise TypeError("Unknown external lookup state")
        if self.state == LookupState.FOUND:
            if self.external_id is None:
                raise ValueError("Found lookup requires an external ID")
            validate_external_id(self.external_id)
        elif self.external_id is not None:
            raise ValueError("Only a found lookup has an external ID")


class DependencyError(DependencyUnavailableError):
    def __init__(self, code: FailureCode) -> None:
        self.code = code
        super().__init__(code.value)


class RecoverableEffect(Protocol):
    async def lookup(self, operation: Operation) -> Lookup:
        """Find the exact permanent identity; never infer absence from a timeout.

        ABSENT must be conclusive. Multiple/mismatched results are CONFLICT. An
        eventually consistent negative or an old in-flight write is UNCERTAIN.
        """
        ...

    async def ensure(self, operation: Operation) -> str:
        """Return a stable ID using upstream atomic uniqueness/idempotency.

        The deterministic identity MUST derive from the permanent source, not the
        lease/request key. Expired workers may still be in flight. A blind create
        is not a valid implementation; lack of proven guarantees blocks wiring.
        """
        ...


class RunResult(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    NEEDS_ATTENTION = "needs_attention"
    LEASE_LOST = "lease_lost"


def result_for(work: Work) -> RunResult:
    return RunResult(work.state.value)


class OperationReservations:
    """Caller-owned UoW collaborator; does not flush/commit unrelated work."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def reserve(self, journal: OperationJournal, spec: OperationSpec) -> Operation:
        now = self.clock.now()
        candidate = Operation(id=uuid4(), spec=spec, work=Work(created_at=now, available_at=now))
        operation = await journal.reserve(candidate)
        if operation.spec != spec:
            raise SourceConflictError("Permanent operation source has different inputs")
        return operation


class RecoveryRunner:
    def __init__(
        self,
        *,
        transactions: OperationTransactionFactory,
        effect: RecoverableEffect,
        clock: Clock,
        owner: str,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.transactions = transactions
        self.effect = effect
        self.clock = clock
        self.owner = owner
        self.policy = policy if policy is not None else RetryPolicy()

    async def run_once(self) -> RunResult:
        async with self.transactions() as transaction:
            operation = await transaction.operations.claim_next(
                now=self.clock.now(), owner=self.owner, policy=self.policy
            )
            # Includes quarantining expired claims which exhausted their budget.
            await transaction.commit()
        if operation is None:
            return RunResult.IDLE
        lease = operation.work.lease
        if lease is None:
            raise ValueError("Journal returned an unclaimed operation")

        # No database transaction/session is open during either external call.
        external_id: str | None = None
        error: FailureCode | None = None
        try:
            external_id = await self.reconcile(operation)
        except DependencyError as failure:
            error = FailureCode(failure.code)
        except TimeoutError:
            error = FailureCode.TIMEOUT
        except Exception:
            # Never persist raw exception messages, credentials or upstream bodies.
            error = FailureCode.INTERNAL
        # CancelledError is intentionally not caught: keep the durable claim until
        # expiry, then reconcile. Cancellation cannot prove the mutation failed.
        now = self.clock.now()
        try:
            work = (
                operation.work.succeed(token=lease.token, now=now)
                if error is None
                else operation.work.fail(token=lease.token, now=now, code=error, policy=self.policy)
            )
        except LeaseLostError:
            return RunResult.LEASE_LOST
        updated = replace(operation, work=work, external_id=external_id)
        async with self.transactions() as transaction:
            if not await transaction.operations.save_leased(updated, token=lease.token, now=self.clock.now()):
                return RunResult.LEASE_LOST
            if work.state == WorkState.SUCCEEDED:
                await transaction.finalize(updated)
            await transaction.commit()
        return result_for(work)

    async def reconcile(self, operation: Operation) -> str:
        async with asyncio.timeout(self.policy.io_timeout.total_seconds()):
            lookup = await self.effect.lookup(operation)
        if lookup.state == LookupState.FOUND and lookup.external_id is not None:
            return lookup.external_id
        if lookup.state == LookupState.UNCERTAIN:
            raise DependencyError(FailureCode.UNCERTAIN)
        if lookup.state == LookupState.CONFLICT:
            raise DependencyError(FailureCode.REJECTED)
        if lookup.state != LookupState.ABSENT:
            raise DependencyError(FailureCode.UNCERTAIN)
        async with asyncio.timeout(self.policy.io_timeout.total_seconds()):
            external_id = await self.effect.ensure(operation)
        validate_external_id(external_id)
        return external_id
