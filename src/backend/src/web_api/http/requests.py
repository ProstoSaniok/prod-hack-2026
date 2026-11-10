"""Bounded exact-Decimal JSON parsing shared by all API routes."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import cast, override

from fastapi import Request
from starlette.types import Receive, Scope

from src.application.catalog.management import CaseValidationError
from src.application.operations.idempotency import (
    CanonicalJsonLimits,
    IdempotencyValidationError,
    canonical_payload_hash,
)


class CaseBodyTooLargeError(Exception):
    pass


class CaseRequest(Request):
    """Bound the stream before FastAPI parsing and retain exact JSON decimals."""

    def __init__(self, scope: Scope, receive: Receive) -> None:
        super().__init__(scope, receive)
        self.case_body: bytes | None = None

    @override
    async def body(self) -> bytes:
        if self.case_body is None:
            content = bytearray()
            async for chunk in self.stream():
                if len(content) + len(chunk) > 65_536:
                    raise CaseBodyTooLargeError
                content.extend(chunk)
            self.case_body = bytes(content)
        return self.case_body

    @override
    async def json(self) -> object:
        raw = await self.body()
        canonical_payload_hash(raw)  # Reject duplicate keys, malformed UTF-8 and unbounded numbers.
        return cast(object, json.loads(raw, parse_float=Decimal))


MAX_REQUEST_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class LearningJsonLimits(CanonicalJsonLimits):
    """Opt in to larger text JSON without relaxing metadata-route defaults."""

    max_input_bytes: int = MAX_REQUEST_BYTES
    max_canonical_bytes: int = 16 * 1024 * 1024

    @override
    def __post_init__(self) -> None:
        CanonicalJsonLimits(
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            max_number_digits=self.max_number_digits,
            max_number_exponent=self.max_number_exponent,
        )
        if (
            type(self.max_input_bytes) is not int
            or not 1 <= self.max_input_bytes <= MAX_REQUEST_BYTES
            or type(self.max_canonical_bytes) is not int
            or not 1 <= self.max_canonical_bytes <= 16 * 1024 * 1024
        ):
            raise IdempotencyValidationError("Invalid learning JSON limits")


JSON_LIMITS = LearningJsonLimits()


class LearningBodyTooLargeError(Exception):
    pass


class LearningRequest(Request):
    def __init__(self, scope: Scope, receive: Receive) -> None:
        super().__init__(scope, receive)
        self.learning_body: bytes | None = None

    @override
    async def body(self) -> bytes:
        if self.learning_body is None:
            content = bytearray()
            async for chunk in self.stream():
                if len(content) + len(chunk) > MAX_REQUEST_BYTES:
                    raise LearningBodyTooLargeError
                content.extend(chunk)
            self.learning_body = bytes(content)
        return self.learning_body

    @override
    async def json(self) -> object:
        raw = await self.body()
        canonical_payload_hash(raw, limits=JSON_LIMITS)
        return cast(object, json.loads(raw, parse_float=Decimal))

    async def prepare(self, *, body_allowed: bool) -> None:
        if len(self.query_params) != len(self.query_params.multi_items()):
            raise CaseValidationError("Duplicate query parameter")
        if self.method in {"POST", "PUT", "PATCH", "DELETE"}:
            raw = await self.body()
            if raw:
                if not body_allowed:
                    raise CaseValidationError("This operation has no request body")
                # Validate before FastAPI turns parser failures into generic 400s.
                await self.json()


def require_unique_query(request: Request) -> None:
    if len(request.query_params) != len(request.query_params.multi_items()):
        raise CaseValidationError("Duplicate query parameter")
