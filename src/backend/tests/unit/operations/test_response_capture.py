"""Focused capture guards; no draft API fixtures or proposed business schemas."""

from dataclasses import replace
from urllib.parse import quote

import pytest

from src.application.operations.idempotency import (
    ArrayShape,
    CachedResponse,
    LocationQueryPolicy,
    ObjectShape,
    RequestIdempotency,
    RequestRecord,
    RequestScope,
    ResponseCaptureError,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)
from tests.support.idempotency import ACTOR, ControlledClock, LockedTestJournal

PATH = "/captures"
STRING = ScalarShape(kinds=frozenset({ScalarKind.STRING}))
ITEM = ObjectShape(fields=(ResponseField(name="id", shape=STRING, required=True),))
POLICY = ResponseCapturePolicy(
    method="POST",
    path=PATH,
    allowed_statuses=frozenset({201}),
    body_shape=ObjectShape(
        fields=(ResponseField(name="items", shape=ArrayShape(items=ITEM, max_items=2), required=True),)
    ),
)
BODY = b' { "items": [{"id": "one"}] }\n'


async def test_nested_response_replays_original_bytes_without_replacing_first_result() -> None:
    handler = RequestIdempotency(clock=ControlledClock(), response_policies=(POLICY,))
    journal = LockedTestJournal()
    scope = RequestScope(actor_id=ACTOR, method="POST", path=PATH, key="capture")
    record = await handler.begin(journal, scope, b"{}")
    assert isinstance(record, RequestRecord)
    assert await handler.complete(journal, record, status_code=201, body=BODY)
    assert not await handler.complete(journal, record, status_code=201, body=b'{"items":[]}')
    assert await handler.begin(journal, scope, b"{ }") == CachedResponse(status_code=201, body=BODY)


@pytest.mark.parametrize(
    "body",
    [
        b'{"items":[{"id":"one","password":"never-store"}]}',
        b'{"items":[{"id":"one","unknown":"never-store"}]}',
        b'{"items":[{"id":"one","\\u0069d":"two"}]}',
        b'{"items":[{}]}',
        b'{"items":[{"id":null}]}',
    ],
)
async def test_nested_unknown_secret_duplicate_and_invalid_fields_never_enter_journal(body: bytes) -> None:
    handler = RequestIdempotency(clock=ControlledClock(), response_policies=(POLICY,))
    journal = LockedTestJournal()
    scope = RequestScope(actor_id=ACTOR, method="POST", path=PATH, key="capture")
    record = await handler.begin(journal, scope, b"{}")
    assert isinstance(record, RequestRecord)
    with pytest.raises(ResponseCaptureError) as error:
        await handler.complete(journal, record, status_code=201, body=body)
    assert "never-store" not in str(error.value)
    assert journal.complete_calls == 0
    assert journal.records[scope] is record


@pytest.mark.parametrize(
    "policy",
    [
        replace(POLICY, max_body_bytes=len(BODY) - 1),
        replace(POLICY, max_body_nodes=3),
        replace(POLICY, max_body_depth=2),
    ],
)
def test_nested_body_byte_node_and_depth_budgets_fail_closed(policy: ResponseCapturePolicy) -> None:
    with pytest.raises(ResponseCaptureError):
        policy.validate(CachedResponse(status_code=201, body=BODY))


def test_array_item_limit_and_required_field_are_independent() -> None:
    POLICY.validate(CachedResponse(status_code=201, body=b'{"items":[]}'))
    with pytest.raises(ResponseCaptureError):
        POLICY.validate(CachedResponse(status_code=201, body=b'{"items":[{"id":"a"},{"id":"b"},{"id":"c"}]}'))
    with pytest.raises(ResponseCaptureError):
        POLICY.validate(CachedResponse(status_code=201, body=b"{}"))


def test_secret_fields_cannot_be_allowed_by_nested_configuration() -> None:
    with pytest.raises(ResponseCaptureError):
        replace(POLICY, body_shape=ObjectShape(fields=(ResponseField(name="password", shape=STRING),)))


def test_location_query_preserves_once_encoded_utf8_and_rejects_other_resources() -> None:
    query = LocationQueryPolicy(path=PATH + "/content", relative_path_keys=frozenset({"path"}))
    location = PATH + "/content?path=" + quote("docs/Привет мир.md", safe="")
    policy = replace(POLICY, location_query=query)
    captured = CachedResponse(status_code=201, body=BODY, location=location)
    policy.validate(captured)
    assert captured.location == location
    with pytest.raises(ResponseCaptureError):
        query.validate("/other/content?path=readme.md")


@pytest.mark.parametrize(
    "suffix",
    [
        "path=readme&path=other",
        "path=readme&access_token=never-store",
        "path=readme\r\nSet-Cookie:never-store",
        "path=%252e%252e%252fprivate",
        "path=" + quote("../private", safe=""),
        "path=" + quote(".git/config", safe=""),
    ],
)
def test_location_query_rejects_ambiguity_injection_and_traversal(suffix: str) -> None:
    query = LocationQueryPolicy(path=PATH + "/content", relative_path_keys=frozenset({"path"}))
    with pytest.raises(ResponseCaptureError):
        query.validate(PATH + "/content?" + suffix)
