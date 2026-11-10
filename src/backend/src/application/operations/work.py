"""Fenced leases and bounded retries shared by journals and outbox delivery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from src.application.common.errors import ConflictError
from src.application.operations.clock import require_aware


class WorkState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    NEEDS_ATTENTION = "needs_attention"


class FailureCode(StrEnum):
    TIMEOUT = "dependency_timeout"
    UNAVAILABLE = "dependency_unavailable"
    UNCERTAIN = "dependency_outcome_unknown"
    REJECTED = "dependency_rejected"
    INTERNAL = "operation_internal_error"
    EXHAUSTED = "operation_attempts_exhausted"


class LeaseLostError(ConflictError):
    """A stale worker must not finalize or reschedule a newer claim."""


@dataclass(frozen=True, kw_only=True)
class Lease:
    token: UUID
    owner: str
    until: datetime

    def __post_init__(self) -> None:
        require_aware(self.until)
        if not self.owner or len(self.owner) > 128:
            raise ValueError("Invalid worker identity")


@dataclass(frozen=True, kw_only=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=1)
    max_delay: timedelta = timedelta(minutes=5)
    lease_for: timedelta = timedelta(seconds=30)
    io_timeout: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("Attempt limit must be between 1 and 100")
        if not timedelta(0) < self.base_delay <= self.max_delay:
            raise ValueError("Invalid retry delays")
        if not timedelta(0) < self.io_timeout * 2 < self.lease_for:
            raise ValueError("Lease must exceed two bounded external calls")

    def delay(self, attempt: int) -> timedelta:
        if not 1 <= attempt <= self.max_attempts:
            raise ValueError("Invalid retry attempt")
        delay = self.base_delay
        for _ in range(attempt - 1):
            # Avoid overflowing timedelta before applying the configured cap.
            if delay >= self.max_delay - delay:
                return self.max_delay
            delay *= 2
        return delay


@dataclass(frozen=True, kw_only=True)
class Work:
    created_at: datetime
    available_at: datetime
    state: WorkState = WorkState.PENDING
    attempts: int = 0
    lease: Lease | None = None
    last_error: FailureCode | None = None

    def __post_init__(self) -> None:
        require_aware(self.created_at)
        require_aware(self.available_at)
        if self.attempts < 0:
            raise ValueError("Negative attempt count")
        if (self.state == WorkState.RUNNING) != (self.lease is not None):
            raise ValueError("Only running work has a lease")

    def is_due(self, now: datetime) -> bool:
        require_aware(now)
        if self.lease is not None:
            return self.lease.until <= now
        return self.state in {WorkState.PENDING, WorkState.RETRY_WAIT} and self.available_at <= now

    def claim(self, *, now: datetime, owner: str, token: UUID, policy: RetryPolicy) -> Work:
        if not self.is_due(now):
            raise ValueError("Work is not due")
        if self.attempts >= policy.max_attempts:
            # A crash on the last attempt may have succeeded remotely. Preserve the
            # permanent source and require operator reconciliation, never recreate.
            return replace(self, state=WorkState.NEEDS_ATTENTION, lease=None, last_error=FailureCode.EXHAUSTED)
        return replace(
            self,
            state=WorkState.RUNNING,
            attempts=self.attempts + 1,
            lease=Lease(token=token, owner=owner, until=now + policy.lease_for),
        )

    def require_lease(self, *, token: UUID, now: datetime) -> None:
        require_aware(now)
        if self.lease is None or self.lease.token != token or self.lease.until <= now:
            raise LeaseLostError("Operation lease expired or was replaced")

    def succeed(self, *, token: UUID, now: datetime) -> Work:
        self.require_lease(token=token, now=now)
        return replace(self, state=WorkState.SUCCEEDED, lease=None, last_error=None)

    def fail(self, *, token: UUID, now: datetime, code: FailureCode, policy: RetryPolicy) -> Work:
        self.require_lease(token=token, now=now)
        terminal = code in {FailureCode.REJECTED, FailureCode.INTERNAL} or self.attempts >= policy.max_attempts
        return replace(
            self,
            state=WorkState.NEEDS_ATTENTION if terminal else WorkState.RETRY_WAIT,
            lease=None,
            available_at=now if terminal else now + policy.delay(self.attempts),
            last_error=code,
        )
