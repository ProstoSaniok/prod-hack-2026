import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from src.application.operations.idempotency import (
    CachedResponse,
    RequestIdempotency,
    RequestRecord,
    RequestScope,
    ResponseCapturePolicy,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


ACTOR = UUID("38521e92-152c-4728-9547-d0ba9de49319")


PATH = "/api/v1/projects"


POLICY = ResponseCapturePolicy(
    method="POST",
    path=PATH,
    allowed_statuses=frozenset({200, 201, 204, 409, 422}),
    allowed_json_fields=frozenset({"id", "count", "ok", "code"}),
    allow_empty_body=True,
    location_prefix=PATH,
)


class ControlledClock:
    __slots__ = ("value",)

    def __init__(self, *, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class LockedTestJournal:
    """Explicit test-only atomicity model, no commits, DB or network access."""

    def __init__(self) -> None:
        self.records: dict[RequestScope, RequestRecord] = {}
        self.lock = asyncio.Lock()
        self.reserve_calls = 0
        self.complete_calls = 0

    async def reserve(self, candidate: RequestRecord, now: datetime) -> RequestRecord:
        async with self.lock:
            self.reserve_calls += 1
            existing = self.records.get(candidate.scope)
            if existing is not None and existing.expires_at > now:
                return existing
            self.records[candidate.scope] = candidate
            return candidate

    async def complete(self, record_id: UUID, response: CachedResponse, now: datetime, expires_at: datetime) -> bool:
        async with self.lock:
            self.complete_calls += 1
            for scope, record in self.records.items():
                if record.id == record_id and record.expires_at > now and record.response is None:
                    self.records[scope] = replace(
                        record, response=response, expires_at=max(record.expires_at, expires_at)
                    )
                    return True
            return False


def scope(**changes: str | UUID) -> RequestScope:
    # Keep construction strictly typed instead of forwarding untyped kwargs.
    actor_id = changes.get("actor_id", ACTOR)
    method = changes.get("method", "POST")
    path = changes.get("path", PATH)
    key = changes.get("key", "request-001")
    assert isinstance(actor_id, UUID)
    assert isinstance(method, str)
    assert isinstance(path, str)
    assert isinstance(key, str)
    return RequestScope(actor_id=actor_id, method=method, path=path, key=key)


def service(clock: ControlledClock | None = None) -> RequestIdempotency:
    return RequestIdempotency(clock=clock or ControlledClock(), response_policies=(POLICY,))


async def reserve(
    handler: RequestIdempotency, journal: LockedTestJournal, *, request_scope: RequestScope | None = None
) -> RequestRecord:
    result = await handler.begin(journal, request_scope or scope(), b'{"name":"demo"}')
    assert isinstance(result, RequestRecord)
    return result
