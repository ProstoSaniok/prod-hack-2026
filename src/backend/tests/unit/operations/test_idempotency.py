"""Isolated contract tests; the lock-backed journal is not a persistence adapter."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Inexact, InvalidOperation, Overflow, Rounded, getcontext, localcontext
from uuid import UUID, uuid4

import pytest

from src.application.operations.idempotency import (
    CANONICAL_PAYLOAD_VERSION,
    MIN_RETENTION,
    CachedResponse,
    CanonicalJsonLimits,
    IdempotencyValidationError,
    InvalidJsonPayloadError,
    PayloadMismatchError,
    RequestIdempotency,
    RequestInProgressError,
    RequestRecord,
    ResponseCaptureError,
    canonical_payload_hash,
)
from tests.support.idempotency import NOW, PATH, POLICY, ControlledClock, LockedTestJournal, reserve, scope, service


def test_canonical_hash_has_explicit_version_domain_separation() -> None:
    assert CANONICAL_PAYLOAD_VERSION == "prodguild-json-v1"
    assert canonical_payload_hash(b"null") == hashlib.sha256(b"prodguild-json-v1\0null").hexdigest()


@pytest.mark.parametrize("rejected", [False, True])
def test_exact_json_number_parsing_preserves_caller_decimal_context(*, rejected: bool) -> None:
    canonical = b'prodguild-json-v1\0{"exp":1e1000,"large":12345678901234567890123456789e0,"tiny":1e-30}'
    expected = hashlib.sha256(canonical).hexdigest()
    with localcontext() as caller:
        caller.prec = 2
        caller.rounding = ROUND_DOWN
        caller.Emax = 2
        caller.Emin = -2
        caller.traps[InvalidOperation] = True
        caller.traps[Inexact] = True
        caller.traps[Rounded] = True
        caller.traps[Overflow] = True
        caller.flags[InvalidOperation] = True
        caller.flags[Inexact] = True
        before_flags, before_traps = caller.flags.copy(), caller.traps.copy()
        if rejected:
            with pytest.raises(InvalidJsonPayloadError):
                canonical_payload_hash(b'{"n":1e9999999999999999999999999999999999999}')
        else:
            assert (
                canonical_payload_hash(b'{"large":12345678901234567890123456789.0000,"tiny":1.0000e-30,"exp":1e1000}')
                == expected
            )
        assert getcontext() is caller
        assert caller.flags == before_flags
        assert caller.traps == before_traps
        assert (caller.prec, caller.rounding, caller.Emax, caller.Emin) == (2, ROUND_DOWN, 2, -2)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (b'{"b":2,"a":1}', b' { "a": 1.0, "b": 2e0 }\n'),
        (b"12345678901234567890123456789", b"12345678901234567890123456789.0"),
    ],
)
def test_canonical_hash_is_deterministic_and_numbers_are_exact(first: bytes, second: bytes) -> None:
    assert canonical_payload_hash(first) == canonical_payload_hash(second)
    assert len(canonical_payload_hash(first)) == 64


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (b"0", b"false"),
        (b"9007199254740992", b"9007199254740993"),
    ],
)
def test_distinct_values_do_not_collapse(first: bytes, second: bytes) -> None:
    assert canonical_payload_hash(first) != canonical_payload_hash(second)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"outer":{"x":1,"\\u0078":2}}',
        b"NaN",
        b"\xff",
        b"[" * 2000 + b"0" + b"]" * 2000,
    ],
)
def test_invalid_or_excessive_json_is_rejected_without_echoing_input(raw: bytes) -> None:
    with pytest.raises(InvalidJsonPayloadError) as error:
        canonical_payload_hash(raw)
    assert "outer" not in str(error.value)


@pytest.mark.parametrize(
    ("raw", "limits"),
    [
        (b"{} ", CanonicalJsonLimits(max_input_bytes=2)),
        (b'"\\u00e9"', CanonicalJsonLimits(max_canonical_bytes=7)),
        (b"[[0]]", CanonicalJsonLimits(max_depth=1)),
        (b"[0,1]", CanonicalJsonLimits(max_nodes=2)),
    ],
)
def test_configured_json_bounds(raw: bytes, limits: CanonicalJsonLimits) -> None:
    with pytest.raises(InvalidJsonPayloadError):
        canonical_payload_hash(raw, limits=limits)


def test_json_boundaries_are_inclusive() -> None:
    assert canonical_payload_hash(b"{}", limits=CanonicalJsonLimits(max_input_bytes=2))
    assert canonical_payload_hash(b"[0]", limits=CanonicalJsonLimits(max_depth=1, max_nodes=2))
    assert canonical_payload_hash(b'"\\u00e9"', limits=CanonicalJsonLimits(max_canonical_bytes=8))


@pytest.mark.parametrize("key", ["x" * 129, "a/b"])
def test_keys_are_bounded_and_safe(key: str) -> None:
    with pytest.raises(IdempotencyValidationError):
        scope(key=key)


@pytest.mark.parametrize(
    "path",
    [
        "/api/../projects",
        "/api/projects?mutate=true",
    ],
)
def test_paths_are_exact_not_silently_normalized(path: str) -> None:
    with pytest.raises(IdempotencyValidationError):
        scope(path=path)


async def test_completion_replays_exact_status_body_and_location() -> None:
    journal = LockedTestJournal()
    handler = service()
    record = await reserve(handler, journal)
    original_body = b' { "id" : "new-project", "ok":true, "count":1.00 }\n'
    location = PATH + "/new-project"
    assert await handler.complete(journal, record, status_code=201, body=original_body, headers={"lOcAtIoN": location})
    replay = await handler.begin(journal, scope(), b' {"name": "demo"} ')
    assert replay == CachedResponse(status_code=201, body=original_body, location=location)
    assert "new-project" not in repr(replay)


async def test_pending_and_changed_body_are_separate_conflicts() -> None:
    journal = LockedTestJournal()
    handler = service()
    record = await reserve(handler, journal)
    with pytest.raises(RequestInProgressError):
        await handler.begin(journal, scope(), b'{"name":"demo"}')
    with pytest.raises(PayloadMismatchError):
        await handler.begin(journal, scope(), b'{"name":"changed"}')
    assert await handler.complete(journal, record, status_code=201, body=b'{"id":"one"}')
    with pytest.raises(PayloadMismatchError):
        await handler.begin(journal, scope(), b'{"name":"changed"}')


@pytest.mark.parametrize("field", ["actor_id", "path"])
async def test_every_scope_dimension_is_isolated(field: str) -> None:
    journal = LockedTestJournal()
    handler = service()
    original = await reserve(handler, journal)
    changes: dict[str, str | UUID] = {"actor_id": uuid4(), "method": "PUT", "path": PATH + "/other", "key": "other"}
    alternative = scope(**{field: changes[field]})
    if field in {"method", "path"}:
        handler = replace(
            handler, response_policies=(POLICY, replace(POLICY, method=alternative.method, path=alternative.path))
        )
    distinct = await reserve(handler, journal, request_scope=alternative)
    assert distinct.id != original.id
    assert len(journal.records) == 2


async def test_completion_extends_retention_from_completion_not_creation() -> None:
    clock = ControlledClock()
    handler = service(clock)
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    clock.value += timedelta(hours=23)
    assert await handler.complete(journal, record, status_code=201, body=b"{}")
    stored = journal.records[scope()]
    assert stored.expires_at == clock.value + MIN_RETENTION
    clock.value = record.expires_at
    assert isinstance(await handler.begin(journal, scope(), b'{"name":"demo"}'), CachedResponse)
    clock.value = stored.expires_at
    replacement = await reserve(handler, journal)
    assert replacement.id != record.id


@pytest.mark.parametrize("status", [401, 403, 503])
async def test_auth_transient_and_unsupported_statuses_are_never_cached(status: int) -> None:
    handler = service()
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=status, body=b"{}")
    assert journal.complete_calls == 0
    assert journal.records[scope()].response is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"access_token":"never-store"}',
        b'{"id":"one","unknown":"never-store"}',
        b'{"id":{"password":"never-store"}}',
    ],
)
async def test_unsafe_response_body_fails_closed_without_redaction(body: bytes) -> None:
    handler = service()
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError) as error:
        await handler.complete(journal, record, status_code=201, body=body)
    assert "never-store" not in str(error.value)
    assert journal.complete_calls == 0
    assert journal.records[scope()].response is None


@pytest.mark.parametrize(
    "headers",
    [
        {"Set-Cookie": "session=never-store"},
        {"Location": PATH, "location": PATH + "/different"},
    ],
)
async def test_arbitrary_or_duplicate_response_headers_are_rejected(headers: dict[str, str]) -> None:
    handler = service()
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=201, body=b"{}", headers=headers)
    assert journal.complete_calls == 0


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/resource",
        "/api/v1/auth/id",
    ],
)
async def test_location_is_safe_and_route_allowlisted(location: str) -> None:
    handler = service()
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=201, body=b"{}", headers={"Location": location})
    assert journal.complete_calls == 0


async def test_response_size_bounds_are_inclusive_and_never_truncate() -> None:
    handler = replace(service(), response_policies=(replace(POLICY, max_body_bytes=12),))
    journal = LockedTestJournal()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=201, body=b'{"id":"1234"}')
    assert journal.complete_calls == 0
    exact = b'{"id":"123"}'
    assert len(exact) == 12
    assert await handler.complete(journal, record, status_code=201, body=exact)
    assert journal.records[scope()].response == CachedResponse(status_code=201, body=exact)
    with pytest.raises(ResponseCaptureError):
        CachedResponse(status_code=201, body=b"x" * (1024 * 1024 + 1))
    with pytest.raises(IdempotencyValidationError):
        replace(POLICY, max_body_bytes=1024 * 1024 + 1)


async def test_empty_response_requires_opt_in_and_204_requires_empty_bytes() -> None:
    journal = LockedTestJournal()
    handler = service()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=204, body=b"{}")
    restricted = replace(handler, response_policies=(replace(POLICY, allow_empty_body=False),))
    with pytest.raises(ResponseCaptureError):
        await restricted.complete(journal, record, status_code=204, body=b"")
    assert await handler.complete(journal, record, status_code=204, body=b"")
    assert await handler.begin(journal, scope(), b'{"name":"demo"}') == CachedResponse(status_code=204, body=b"")


async def test_route_and_status_require_explicit_allowlisting_before_storage() -> None:
    journal = LockedTestJournal()
    handler = RequestIdempotency(clock=ControlledClock())
    with pytest.raises(ResponseCaptureError):
        await handler.begin(journal, scope(), b"{}")
    assert journal.reserve_calls == 0
    with pytest.raises(ResponseCaptureError):
        replace(service(), response_policies=(POLICY, POLICY))
    handler = service()
    record = await reserve(handler, journal)
    with pytest.raises(ResponseCaptureError):
        await handler.complete(journal, record, status_code=202, body=b"{}")
    assert journal.complete_calls == 0


@pytest.mark.parametrize("path", ["/auth/sign_in/email", "/oauth/token"])
def test_authentication_routes_cannot_opt_in_to_response_caching(path: str) -> None:
    with pytest.raises(ResponseCaptureError):
        replace(POLICY, path=path)


async def test_mutating_query_parameters_in_the_boundary_envelope_affect_hash() -> None:
    handler = service()
    journal = LockedTestJournal()
    request_scope = scope()
    assert isinstance(
        await handler.begin(journal, request_scope, b'{"body":{},"query":{"priority":false}}'), RequestRecord
    )
    with pytest.raises(PayloadMismatchError):
        await handler.begin(journal, request_scope, b'{"body":{},"query":{"priority":true}}')


async def test_replay_revalidates_policy_instead_of_exposing_old_now_forbidden_content() -> None:
    journal = LockedTestJournal()
    handler = service()
    record = await reserve(handler, journal)
    assert await handler.complete(journal, record, status_code=201, body=b'{"id":"one"}')
    stricter = replace(handler, response_policies=(replace(POLICY, allowed_json_fields=frozenset()),))
    with pytest.raises(ResponseCaptureError):
        await stricter.begin(journal, scope(), b'{"name":"demo"}')


async def test_bad_json_is_rejected_before_reservation() -> None:
    journal = LockedTestJournal()
    with pytest.raises(InvalidJsonPayloadError):
        await service().begin(journal, scope(), b'{"secret":"never-store","secret":0}')
    assert journal.reserve_calls == 0


async def test_aware_non_utc_clock_is_accepted_and_clock_errors_fail_closed() -> None:
    journal = LockedTestJournal()
    clock = ControlledClock(value=NOW.astimezone(timezone(timedelta(hours=3))))
    handler = service(clock)
    record = await reserve(handler, journal)
    assert record.created_at == NOW
    assert record.created_at.tzinfo is UTC
    clock.value = NOW - timedelta(seconds=1)
    with pytest.raises(IdempotencyValidationError):
        await handler.complete(journal, record, status_code=201, body=b"{}")
    clock.value = NOW.replace(tzinfo=None)
    with pytest.raises(IdempotencyValidationError):
        await handler.begin(journal, scope(), b"{}")
    with pytest.raises(IdempotencyValidationError):
        await handler.complete(journal, record, status_code=201, body=b"{}")
    assert journal.complete_calls == 0
    clock.value = datetime.max.replace(tzinfo=UTC)
    with pytest.raises(IdempotencyValidationError):
        await handler.begin(journal, scope(), b"{}")
