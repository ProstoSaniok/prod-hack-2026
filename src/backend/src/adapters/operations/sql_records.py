"""Core row conversion for immutable operation DTOs; never serialize replay JSON."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.application.operations.entity import Operation, OperationSpec, OutboxMessage, ProjectionSpec
from src.application.operations.idempotency import (
    CANONICAL_PAYLOAD_VERSION,
    CachedResponse,
    IdempotencyValidationError,
    RequestRecord,
    RequestScope,
)
from src.application.operations.recovery import validate_external_id
from src.application.operations.work import FailureCode, Lease, Work, WorkState


class Row(Protocol):
    def __getitem__(self, key: str, /) -> object: ...


def value[T](row: Row, name: str, expected: type[T]) -> T:
    item = row[name]
    if not isinstance(item, expected) or (expected is int and isinstance(item, bool)):
        raise ValueError("Invalid persisted operation field")
    return item


def optional[T](row: Row, name: str, expected: type[T]) -> T | None:
    return None if row[name] is None else value(row, name, expected)


def work_from_row(row: Row) -> Work:
    token = optional(row, "lease_token", UUID)
    owner = optional(row, "lease_owner", str)
    until = optional(row, "lease_until", datetime)
    if (token is None) != (owner is None) or (token is None) != (until is None):
        raise ValueError("Incomplete persisted operation lease")
    lease = None
    if token is not None and owner is not None and until is not None:
        lease = Lease(token=token, owner=owner, until=until)
    return Work(
        created_at=value(row, "created_at", datetime),
        available_at=value(row, "available_at", datetime),
        state=value(row, "state", WorkState),
        attempts=value(row, "attempts", int),
        lease=lease,
        last_error=optional(row, "last_error", FailureCode),
    )


def work_values(work: Work) -> dict[str, object]:
    return {
        "created_at": work.created_at,
        "available_at": work.available_at,
        "state": work.state,
        "attempts": work.attempts,
        "lease_token": work.lease.token if work.lease else None,
        "lease_owner": work.lease.owner if work.lease else None,
        "lease_until": work.lease.until if work.lease else None,
        "last_error": work.last_error,
    }


def operation_from_row(row: Row) -> Operation:
    external_id = optional(row, "external_id", str)
    if external_id is not None:
        validate_external_id(external_id)
    return Operation(
        id=value(row, "id", UUID),
        spec=OperationSpec(
            kind=value(row, "kind", str),
            source_key=value(row, "source_key", str),
            subject_id=value(row, "subject_id", UUID),
            payload_hash=value(row, "payload_hash", str),
        ),
        work=work_from_row(row),
        external_id=external_id,
    )


def projection_from_row(row: Row) -> ProjectionSpec:
    return ProjectionSpec(
        topic=value(row, "topic", str),
        source_key=value(row, "source_key", str),
        subject_id=value(row, "subject_id", UUID),
        revision=value(row, "revision", int),
    )


def outbox_from_row(row: Row) -> OutboxMessage:
    return OutboxMessage(id=value(row, "id", UUID), spec=projection_from_row(row), work=work_from_row(row))


def request_from_row(row: Row, scope: RequestScope) -> RequestRecord:
    if row["canonical_payload_version"] != CANONICAL_PAYLOAD_VERSION:
        raise IdempotencyValidationError("Unsupported persisted request hash version")
    status = optional(row, "response_status", int)
    body = optional(row, "response_body", bytes)
    location = optional(row, "response_location", str)
    name = optional(row, "response_queue_header_name", str)
    header_value = optional(row, "response_queue_header_value", str)
    if (status is None) != (body is None) or (name is None) != (header_value is None):
        raise IdempotencyValidationError("Incomplete persisted replay outcome")
    if status is None and (location is not None or name is not None):
        raise IdempotencyValidationError("Pending request contains replay headers")
    response = None
    if status is not None and body is not None:
        response = CachedResponse(
            status_code=status,
            body=body,
            location=location,
            queue_state_header=(name, header_value) if name is not None and header_value is not None else None,
        )
    return RequestRecord(
        id=value(row, "id", UUID),
        scope=scope,
        payload_hash=value(row, "payload_hash", str),
        created_at=value(row, "created_at", datetime),
        expires_at=value(row, "expires_at", datetime),
        response=response,
    )


def scope_from_row(row: Row) -> RequestScope:
    return RequestScope(
        actor_id=value(row, "actor_id", UUID),
        method=value(row, "method", str),
        path=value(row, "path", str),
        key=value(row, "request_key", str),
    )
