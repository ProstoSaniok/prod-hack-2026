"""Exact response serialization and retained idempotency receipts."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Response

from src.application.operations.idempotency import CachedResponse
from src.application.repository_storage.ports import StorageError


def present(response: CachedResponse, *, replay: bool = False) -> Response:
    return Response(
        content=response.body,
        status_code=200 if replay and response.status_code == 201 else response.status_code,
        media_type="application/json" if response.status_code != 204 else None,
        headers={"Location": response.location} if response.location else None,
    )


def review_response(response: CachedResponse) -> Response:
    result = present(response)
    if response.queue_state_header is not None:
        name, value = response.queue_state_header
        result.headers[name] = value
    return result


MAX_FILE_RESPONSE_BYTES = 2 * 1024 * 1024


def json_response(body: bytes, *, files: bool = False) -> Response:
    if files and len(body) > MAX_FILE_RESPONSE_BYTES:
        raise StorageError
    return Response(content=body, media_type="application/json")


def encode_exact_json(value: object) -> bytes:
    """Emit exact Decimal JSON numbers, matching existing review/progression reads."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Expected finite profile number")
        return str(value).encode("ascii")
    if isinstance(value, dict):
        return (
            b"{"
            + b",".join(encode_exact_json(key) + b":" + encode_exact_json(item) for key, item in value.items())
            + b"}"
        )
    if isinstance(value, (tuple, list)):
        return b"[" + b",".join(encode_exact_json(item) for item in value) + b"]"
    if isinstance(value, (UUID, datetime)):
        value = value.isoformat() if isinstance(value, datetime) else str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
