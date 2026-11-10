"""Focused persistence decoding failures; these do not prove PostgreSQL behavior."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.adapters.operations.sql_records import request_from_row, work_from_row
from src.application.operations.idempotency import IdempotencyValidationError, RequestScope, ResponseCaptureError
from src.application.operations.work import WorkState


def test_persisted_request_preserves_bytes_and_rejects_partial_or_unknown_outcomes() -> None:
    now = datetime.now(UTC)
    scope = RequestScope(actor_id=uuid4(), method="POST", path="/projects", key="sql-record")
    body = b' { "id": "original" }\n'
    row: dict[str, object] = {
        "id": uuid4(),
        "payload_hash": "0" * 64,
        "canonical_payload_version": "prodguild-json-v1",
        "created_at": now,
        "expires_at": now + timedelta(days=1),
        "response_status": 201,
        "response_body": body,
        "response_location": "/projects/one",
        "response_queue_header_name": "x-QuEuE-sTaTe",
        "response_queue_header_value": "waiting",
    }
    record = request_from_row(row, scope)
    assert record.response is not None
    assert record.response.status_code == 201
    assert record.response.body is body
    assert record.response.location == "/projects/one"
    assert record.response.queue_state_header == ("x-QuEuE-sTaTe", "waiting")
    with pytest.raises(IdempotencyValidationError, match="hash version"):
        request_from_row({**row, "canonical_payload_version": "unsupported"}, scope)
    with pytest.raises(IdempotencyValidationError, match="Incomplete"):
        request_from_row({**row, "response_queue_header_value": None}, scope)
    with pytest.raises(ResponseCaptureError) as error:
        request_from_row({**row, "response_queue_header_value": "waiting\r\nAuthorization: never-store"}, scope)
    assert "never-store" not in str(error.value)


def test_empty_completed_body_is_distinct_from_pending_and_no_header_is_invented() -> None:
    now = datetime.now(UTC)
    scope = RequestScope(actor_id=uuid4(), method="POST", path="/assignments", key="empty")
    row: dict[str, object] = {
        "id": uuid4(),
        "payload_hash": "0" * 64,
        "canonical_payload_version": "prodguild-json-v1",
        "created_at": now,
        "expires_at": now + timedelta(days=1),
        "response_status": None,
        "response_body": None,
        "response_location": None,
        "response_queue_header_name": None,
        "response_queue_header_value": None,
    }
    assert request_from_row(row, scope).response is None
    completed = request_from_row({**row, "response_status": 204, "response_body": b""}, scope).response
    assert completed is not None
    assert completed.body == b""
    assert completed.queue_state_header is None
    with pytest.raises(IdempotencyValidationError):
        request_from_row({**row, "response_status": 204}, scope)
    with pytest.raises(IdempotencyValidationError):
        request_from_row({**row, "response_location": "/assignments/private"}, scope)


def test_work_decode_rejects_partial_lease_and_untyped_enum_without_exposing_values() -> None:
    now = datetime.now(UTC)
    row: dict[str, object] = {
        "created_at": now,
        "available_at": now,
        "state": WorkState.RUNNING,
        "attempts": 1,
        "lease_token": uuid4(),
        "lease_owner": "test-worker",
        "lease_until": now + timedelta(seconds=30),
        "last_error": None,
    }
    assert work_from_row(row).lease is not None
    with pytest.raises(ValueError, match="Incomplete"):
        work_from_row({**row, "lease_until": None})
    with pytest.raises(ValueError, match="persisted operation field"):
        work_from_row({**row, "state": "never-store"})
