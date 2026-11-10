"""Replay presentation preserves the captured outcome and exact route policy."""

from dataclasses import replace

import pytest

from src.application.operations.idempotency import (
    CachedResponse,
    QueueStatePolicy,
    QueueStateRule,
    RequestIdempotency,
    RequestRecord,
    RequestScope,
    ResponseCaptureError,
    ResponseCapturePolicy,
)
from src.application.operations.replay_binding import ReplayBinding, ReplayStatusMode
from tests.support.idempotency import ACTOR, ControlledClock, LockedTestJournal

PATH = "/captures"
BODY = b' { "id": "original" }\n'
QUEUE = QueueStatePolicy(rules=(QueueStateRule(status_code=201, allowed_values=frozenset({"waiting"})),))
POLICY = ResponseCapturePolicy(
    method="POST",
    path=PATH,
    allowed_statuses=frozenset({201, 409}),
    allowed_json_fields=frozenset({"id"}),
    location_prefix=PATH,
    queue_state_policy=QUEUE,
)
SCOPE = RequestScope(actor_id=ACTOR, method="POST", path=PATH, key="replay")


async def test_created_as_ok_changes_only_replay_presentation_not_journal_or_headers() -> None:
    handler = RequestIdempotency(clock=ControlledClock(), response_policies=(POLICY,))
    journal = LockedTestJournal()
    record = await handler.begin(journal, SCOPE, b"{}")
    assert isinstance(record, RequestRecord)
    headers = {"Location": PATH + "/original", "x-QuEuE-sTaTe": "waiting"}
    assert await handler.complete(journal, record, status_code=201, body=BODY, headers=headers)
    headers["x-QuEuE-sTaTe"] = "changed"
    captured = await handler.begin(journal, SCOPE, b"{ }")
    assert isinstance(captured, CachedResponse)
    binding = ReplayBinding(capture_policy=POLICY, replay_status=ReplayStatusMode.CREATED_AS_OK)
    assert binding.first(SCOPE, captured).status_code == 201
    assert ReplayBinding(capture_policy=POLICY).replay(SCOPE, captured).status_code == 201
    replay = binding.replay(SCOPE, captured)
    assert replay.status_code == 200
    assert replay.captured is captured
    assert captured.status_code == 201
    assert captured.body is BODY
    assert captured.location == PATH + "/original"
    assert captured.queue_state_header == ("x-QuEuE-sTaTe", "waiting")
    assert journal.records[SCOPE].response is captured
    assert journal.complete_calls == 1


def test_business_error_is_not_remapped_to_success() -> None:
    captured = CachedResponse(status_code=409, body=BODY)
    binding = ReplayBinding(capture_policy=POLICY, replay_status=ReplayStatusMode.CREATED_AS_OK)
    assert binding.replay(SCOPE, captured).status_code == 409
    assert binding.first(SCOPE, captured).captured is captured


@pytest.mark.parametrize(
    "headers",
    [
        None,
        (("X-Queue-State", "waiting"), ("x-queue-state", "waiting")),
        {"X-Queue-State": "assigned"},
        {"X-Queue-State": "waiting\r\nSet-Cookie:never-store"},
    ],
)
async def test_missing_duplicate_or_unapproved_queue_header_never_completes(
    headers: dict[str, str] | tuple[tuple[str, str], ...] | None,
) -> None:
    handler = RequestIdempotency(clock=ControlledClock(), response_policies=(POLICY,))
    journal = LockedTestJournal()
    record = await handler.begin(journal, SCOPE, b"{}")
    assert isinstance(record, RequestRecord)
    with pytest.raises(ResponseCaptureError) as error:
        await handler.complete(journal, record, status_code=201, body=BODY, headers=headers)
    assert "never-store" not in str(error.value)
    assert journal.complete_calls == 0
    assert journal.records[SCOPE] is record


def test_queue_header_is_default_deny() -> None:
    captured = CachedResponse(status_code=201, body=BODY, queue_state_header=("X-Queue-State", "waiting"))
    with pytest.raises(ResponseCaptureError):
        replace(POLICY, queue_state_policy=None).validate(captured)


@pytest.mark.parametrize("scope", [replace(SCOPE, method="PUT"), replace(SCOPE, path=PATH + "/other")])
def test_both_presentations_require_exact_route(scope: RequestScope) -> None:
    captured = CachedResponse(status_code=201, body=BODY, queue_state_header=("X-Queue-State", "waiting"))
    binding = ReplayBinding(capture_policy=POLICY, replay_status=ReplayStatusMode.CREATED_AS_OK)
    with pytest.raises(ResponseCaptureError):
        binding.first(scope, captured)
    with pytest.raises(ResponseCaptureError):
        binding.replay(scope, captured)


async def test_tightened_policy_rejects_replay_without_overwriting_snapshot() -> None:
    handler = RequestIdempotency(clock=ControlledClock(), response_policies=(POLICY,))
    journal = LockedTestJournal()
    record = await handler.begin(journal, SCOPE, b"{}")
    assert isinstance(record, RequestRecord)
    assert await handler.complete(journal, record, status_code=201, body=BODY, headers={"X-Queue-State": "waiting"})
    captured = journal.records[SCOPE].response
    tightened = replace(POLICY, queue_state_policy=None)
    with pytest.raises(ResponseCaptureError):
        await replace(handler, response_policies=(tightened,)).begin(journal, SCOPE, b"{}")
    assert journal.records[SCOPE].response is captured
    assert await handler.begin(journal, SCOPE, b"{}") is captured
