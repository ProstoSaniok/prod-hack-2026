"""Bounded HTTP request replay, not permanent business-effect deduplication.

The HTTP boundary MUST authenticate and check current ownership/permissions before
``begin``, including on a replay. Paths are exact canonical resource paths, not
route templates. Mutating query parameters MUST enter the hashed JSON envelope;
the scope deliberately accepts no query string. Empty requests need an explicit
JSON representation (for example ``null``).

The caller owns the transaction. Reservation, business changes and completion must
use the appropriate caller-owned transactional journal; nothing here commits.
Expiring request keys cannot protect permanent rewards, repository creation or
other business effects: those need separate durable business identities.

Request numbers use exact Decimal coefficients/exponents, without float conversion
or Decimal-context rounding. Trailing coefficient zeros and negative zero are
normalized: 1, 1.0 and 1e0 hash equally. Strings are NOT Unicode-normalized. This is
a private deterministic format, not RFC 8785. Responses are never reserialized or
redacted. Flat field policies remain supported; nested objects/arrays and file
Location queries require explicit bounded shape and exact-destination policies.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import quote, unquote_to_bytes
from uuid import UUID, uuid4

from src.application.common.errors import ConflictError, ValidationError
from src.application.operations.clock import Clock

__all__ = [
    "CANONICAL_PAYLOAD_VERSION",
    "MIN_RETENTION",
    "ArrayShape",
    "CachedResponse",
    "CanonicalJsonLimits",
    "IdempotencyValidationError",
    "InvalidJsonPayloadError",
    "LocationQueryPolicy",
    "ObjectShape",
    "PayloadMismatchError",
    "QueueStatePolicy",
    "QueueStateRule",
    "RequestIdempotency",
    "RequestInProgressError",
    "RequestJournal",
    "RequestRecord",
    "RequestScope",
    "ResponseCaptureError",
    "ResponseCapturePolicy",
    "ResponseField",
    "ResponseShape",
    "ScalarKind",
    "ScalarShape",
    "canonical_payload_hash",
]

CANONICAL_PAYLOAD_VERSION = "prodguild-json-v1"
MIN_RETENTION = timedelta(hours=24)
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_PATH_LENGTH = 2048
_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_UNCACHEABLE_STATUSES = frozenset({206, 304, 401, 403, 407, 408, 425, 429})
_SECRET_MARKERS = (
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "authorization",
    "authentication",
    "cookie",
    "session",
    "apikey",
    "privatekey",
    "bearer",
    "jwt",
    "csrf",
    "xsrf",
    "signingkey",
    "encryptionkey",
)

type _JsonValue = None | bool | str | Decimal | list[_JsonValue] | dict[str, _JsonValue]


class IdempotencyValidationError(ValidationError):
    """Invalid configuration, scope, clock or journal contract; never includes input."""


class InvalidJsonPayloadError(IdempotencyValidationError):
    pass


class ResponseCaptureError(IdempotencyValidationError):
    pass


class PayloadMismatchError(ConflictError):
    """A live request key was reused with a different canonical payload."""


class RequestInProgressError(ConflictError):
    """The original request is still pending; do not repeat its business effects."""


def _bounded_integer(value: int, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise IdempotencyValidationError("Invalid idempotency limit")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IdempotencyValidationError("Idempotency requires an aware clock")
    return value.astimezone(UTC)


def _canonical_path(path: str) -> None:
    # Reject aliases instead of normalizing: no escapes, authority, query, fragment,
    # dot segments, repeated/trailing slash, backslash, controls or non-ASCII input.
    if (
        not 1 <= len(path) <= _MAX_PATH_LENGTH
        or re.fullmatch(r"/[A-Za-z0-9._~/-]*", path) is None
        or "//" in path
        or (path != "/" and path.endswith("/"))
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise IdempotencyValidationError("Expected an exact canonical path")


def _route(method: str, path: str) -> None:
    if method not in _METHODS:
        raise IdempotencyValidationError("Expected an uppercase mutating HTTP method")
    _canonical_path(path)


@dataclass(frozen=True, kw_only=True, slots=True)
class RequestScope:
    actor_id: UUID
    method: str
    path: str
    key: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.actor_id) is not UUID or self.actor_id.int == 0:
            raise IdempotencyValidationError("Expected an actor UUID")
        _route(self.method, self.path)
        if re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", self.key) is None:
            raise IdempotencyValidationError("Invalid request key")


@dataclass(frozen=True, kw_only=True, slots=True)
class CanonicalJsonLimits:
    max_input_bytes: int = 64 * 1024
    max_canonical_bytes: int = 256 * 1024
    max_depth: int = 32
    max_nodes: int = 10_000
    max_number_digits: int = 128
    max_number_exponent: int = 1000

    def __post_init__(self) -> None:
        _bounded_integer(self.max_input_bytes, maximum=1024 * 1024)
        _bounded_integer(self.max_canonical_bytes, maximum=1024 * 1024)
        _bounded_integer(self.max_depth, maximum=64)
        _bounded_integer(self.max_nodes, maximum=100_000)
        _bounded_integer(self.max_number_digits, maximum=1024)
        _bounded_integer(self.max_number_exponent, maximum=10_000)


_DEFAULT_JSON_LIMITS = CanonicalJsonLimits()


def _unique_object(pairs: list[tuple[str, _JsonValue]]) -> dict[str, _JsonValue]:
    result: dict[str, _JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidJsonPayloadError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> _JsonValue:
    raise InvalidJsonPayloadError("Nonfinite JSON number")


def _parse_json(raw: bytes, limits: CanonicalJsonLimits) -> _JsonValue:
    if type(raw) is not bytes or not raw or len(raw) > limits.max_input_bytes:
        raise InvalidJsonPayloadError("JSON input is empty or exceeds its byte limit")
    try:
        # The cast narrows json.loads' dynamic return. All parser-produced value
        # types are fixed here, then checked by the bounded canonical traversal.
        # Decimal(string) is exact but an excessive exponent can signal sticky
        # InvalidOperation. Contain all parser flags/traps in a private copy, and
        # reject invalid construction even if the caller disables that trap.
        # No unary arithmetic/normalization or precision rounding is performed.
        with localcontext() as context:
            context.traps[InvalidOperation] = True
            return cast(
                _JsonValue,
                json.loads(
                    raw.decode("utf-8"),
                    parse_int=Decimal,
                    parse_float=Decimal,
                    parse_constant=_reject_constant,
                    object_pairs_hook=_unique_object,
                ),
            )
    except (UnicodeError, ValueError, DecimalException, RecursionError):
        raise InvalidJsonPayloadError("Invalid or excessive JSON input") from None


def _json_string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidJsonPayloadError("Invalid Unicode in JSON") from None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _json_number(value: Decimal, limits: CanonicalJsonLimits) -> str:
    parts = value.as_tuple()
    if (
        not value.is_finite()
        or not isinstance(parts.exponent, int)
        or len(parts.digits) > limits.max_number_digits
        or abs(parts.exponent) > limits.max_number_exponent
    ):
        raise InvalidJsonPayloadError("JSON number exceeds its bounds")
    coefficient = "".join(str(digit) for digit in parts.digits).rstrip("0")
    if not coefficient:
        return "0"
    exponent = parts.exponent + len(parts.digits) - len(coefficient)
    return f"{'-' if parts.sign else ''}{coefficient}e{exponent}"


def _canonical_json(value: _JsonValue, limits: CanonicalJsonLimits) -> bytes:
    remaining = limits.max_nodes

    def visit(node: _JsonValue, depth: int) -> str:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > limits.max_depth:
            raise InvalidJsonPayloadError("JSON structure exceeds its bounds")
        if node is None:
            return "null"
        if isinstance(node, bool):
            return "true" if node else "false"
        if isinstance(node, str):
            return _json_string(node)
        if isinstance(node, Decimal):
            return _json_number(node, limits)
        if isinstance(node, list):
            return "[" + ",".join(visit(item, depth + 1) for item in node) + "]"
        return "{" + ",".join(_json_string(key) + ":" + visit(node[key], depth + 1) for key in sorted(node)) + "}"

    result = visit(value, 0).encode("ascii")
    if len(result) > limits.max_canonical_bytes:
        raise InvalidJsonPayloadError("Canonical JSON exceeds its byte limit")
    return result


def canonical_payload_hash(raw_json: bytes, *, limits: CanonicalJsonLimits = _DEFAULT_JSON_LIMITS) -> str:
    """Hash version + NUL + strict canonical JSON; never retain the raw body."""
    canonical = _canonical_json(_parse_json(raw_json, limits), limits)
    return hashlib.sha256(CANONICAL_PAYLOAD_VERSION.encode("ascii") + b"\0" + canonical).hexdigest()


def _cacheable_status(status_code: int) -> None:
    # Deny all 5xx, authentication/authorization failures, transient 4xx and
    # partial/conditional responses which require unsupported response headers.
    if type(status_code) is not int or not 200 <= status_code < 500 or status_code in _UNCACHEABLE_STATUSES:
        raise ResponseCaptureError("Response status is not safe for replay")


@dataclass(frozen=True, kw_only=True, slots=True)
class CachedResponse:
    status_code: int
    body: bytes = field(repr=False)
    location: str | None = field(default=None, repr=False)
    queue_state_header: tuple[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _cacheable_status(self.status_code)
        if type(self.body) is not bytes or len(self.body) > _MAX_RESPONSE_BYTES:
            raise ResponseCaptureError("Response exceeds its byte limit")
        if self.status_code in {204, 205} and self.body:
            raise ResponseCaptureError("This response status requires an empty body")
        if self.location is not None:
            _location_parts(self.location)
        if self.queue_state_header is not None:
            _validate_queue_header(self.queue_state_header)


def _header_pair(entry: object) -> tuple[str, str]:
    if type(entry) is not tuple or len(entry) != 2:
        raise ResponseCaptureError("Invalid application header representation")
    name, value = entry
    if type(name) is not str or type(value) is not str:
        raise ResponseCaptureError("Invalid application header representation")
    return name, value


def _validate_queue_header(header: tuple[str, str]) -> None:
    name, value = _header_pair(header)
    if not name.isascii() or name.lower() != "x-queue-state" or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value) is None:
        raise ResponseCaptureError("Invalid bounded queue-state header")


@dataclass(frozen=True, kw_only=True, slots=True)
class QueueStateRule:
    status_code: int
    allowed_values: frozenset[str]
    required: bool = True

    def __post_init__(self) -> None:
        _cacheable_status(self.status_code)
        if type(self.allowed_values) is not frozenset or not 1 <= len(self.allowed_values) <= 16:
            raise ResponseCaptureError("Expected a bounded immutable queue-state vocabulary")
        for value in self.allowed_values:
            _validate_queue_header(("X-Queue-State", value))
        if type(self.required) is not bool:
            raise ResponseCaptureError("Queue-state required flag must be boolean")


@dataclass(frozen=True, kw_only=True, slots=True)
class QueueStatePolicy:
    """Exact route opt-in to finite, status-specific, reviewed header values.

    No global queue state machine or accepted API vocabulary is inferred here.
    Values are exact, never trimmed, folded, split or regenerated from current
    queue state. Rules refer to the retained original status, before presentation.
    """

    rules: tuple[QueueStateRule, ...]

    def __post_init__(self) -> None:
        if type(self.rules) is not tuple or not 1 <= len(self.rules) <= 16:
            raise ResponseCaptureError("Expected bounded immutable queue-state rules")
        if any(type(rule) is not QueueStateRule for rule in self.rules):
            raise ResponseCaptureError("Unknown queue-state rule")
        if len({rule.status_code for rule in self.rules}) != len(self.rules):
            raise ResponseCaptureError("Duplicate queue-state status rule")

    def validate(self, response: CachedResponse) -> None:
        rule = next((rule for rule in self.rules if rule.status_code == response.status_code), None)
        header = response.queue_state_header
        if header is None:
            if rule is not None and rule.required:
                raise ResponseCaptureError("Required queue-state header is missing")
        elif rule is None or header[1] not in rule.allowed_values:
            raise ResponseCaptureError("Queue-state value is outside its route vocabulary")


def _secret_field(name: str) -> bool:
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    return normalized in {"auth", "pwd", "salt"} or any(marker in normalized for marker in _SECRET_MARKERS)


def _safe_field(name: str) -> None:
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) is None or _secret_field(name):
        raise ResponseCaptureError("Unsafe response field allowlist")


class ScalarKind(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    NULL = "null"


@dataclass(frozen=True, kw_only=True, slots=True)
class ScalarShape:
    kinds: frozenset[ScalarKind]
    max_string_bytes: int = 16 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.kinds) is not frozenset
            or not self.kinds
            or any(type(kind) is not ScalarKind for kind in self.kinds)
        ):
            raise ResponseCaptureError("Expected explicit immutable scalar kinds")
        _bounded_integer(self.max_string_bytes, maximum=_MAX_RESPONSE_BYTES)


@dataclass(frozen=True, kw_only=True, slots=True)
class ResponseField:
    name: str
    shape: ResponseShape
    required: bool = False

    def __post_init__(self) -> None:
        _safe_field(self.name)
        if type(self.required) is not bool:
            raise ResponseCaptureError("Field required flag must be boolean")


@dataclass(frozen=True, kw_only=True, slots=True)
class ObjectShape:
    fields: tuple[ResponseField, ...]
    nullable: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.fields) is not tuple
            or len(self.fields) > 128
            or any(type(item) is not ResponseField for item in self.fields)
        ):
            raise ResponseCaptureError("Expected bounded immutable object fields")
        if len({item.name for item in self.fields}) != len(self.fields) or type(self.nullable) is not bool:
            raise ResponseCaptureError("Invalid object shape configuration")


@dataclass(frozen=True, kw_only=True, slots=True)
class ArrayShape:
    items: ResponseShape
    max_items: int
    nullable: bool = False

    def __post_init__(self) -> None:
        _bounded_integer(self.max_items, maximum=10_000)
        if type(self.nullable) is not bool:
            raise ResponseCaptureError("Array nullable flag must be boolean")


type ResponseShape = ScalarShape | ObjectShape | ArrayShape


def _validate_shape_configuration(shape: ResponseShape) -> None:
    remaining = 1024

    def visit(node: ResponseShape, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ResponseCaptureError("Response shape configuration exceeds its bounds")
        # Exact built-in frozen nodes only: no mutable mappings, arbitrary
        # validators or subclasses that can relax capture policy.
        if type(node) is ScalarShape:
            return
        if type(node) is ObjectShape:
            for item in node.fields:
                visit(item.shape, depth + 1)
        elif type(node) is ArrayShape:
            visit(node.items, depth + 1)
        else:
            raise ResponseCaptureError("Unknown response shape")

    visit(shape, 0)


def _scalar_matches(value: _JsonValue, shape: ScalarShape) -> bool:
    if value is None:
        return ScalarKind.NULL in shape.kinds
    if isinstance(value, bool):
        return ScalarKind.BOOLEAN in shape.kinds
    if isinstance(value, str):
        return ScalarKind.STRING in shape.kinds and len(value.encode("utf-8")) <= shape.max_string_bytes
    if isinstance(value, Decimal):
        _json_number(value, _DEFAULT_JSON_LIMITS)
        return ScalarKind.NUMBER in shape.kinds or (
            ScalarKind.INTEGER in shape.kinds and value == value.to_integral_value()
        )
    return False


def _shape_children(node: _JsonValue, rule: ResponseShape) -> Iterator[tuple[_JsonValue, ResponseShape]]:
    if isinstance(rule, ScalarShape):
        if not _scalar_matches(node, rule):
            raise ResponseCaptureError("Response scalar does not match its shape")
    elif node is None and rule.nullable:
        return
    elif isinstance(rule, ObjectShape) and isinstance(node, dict):
        yield from _object_children(node, rule)
    elif isinstance(rule, ArrayShape) and isinstance(node, list):
        if len(node) > rule.max_items:
            raise ResponseCaptureError("Response array exceeds its item limit")
        for item in node:
            yield item, rule.items
    else:
        raise ResponseCaptureError("Response does not match its object or array shape")


def _object_children(node: dict[str, _JsonValue], rule: ObjectShape) -> Iterator[tuple[_JsonValue, ResponseShape]]:
    fields = {item.name: item for item in rule.fields}
    if any(item.required and item.name not in node for item in rule.fields):
        raise ResponseCaptureError("Response is missing required fields")
    for key, item in node.items():
        if key not in fields or _secret_field(key):
            raise ResponseCaptureError("Response contains non-allowlisted content")
        yield item, fields[key].shape


def _validate_response_shape(value: _JsonValue, shape: ResponseShape, *, max_depth: int, max_nodes: int) -> None:
    remaining = max_nodes

    def visit(node: _JsonValue, rule: ResponseShape, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > max_depth:
            raise ResponseCaptureError("Response structure exceeds its bounds")
        for child, child_rule in _shape_children(node, rule):
            visit(child, child_rule, depth + 1)

    try:
        visit(value, shape, 0)
    except (UnicodeError, InvalidJsonPayloadError):
        raise ResponseCaptureError("Response contains invalid bounded JSON values") from None


def _location_parts(location: str) -> tuple[str, str | None]:
    # Manual split avoids URL parsers silently stripping control characters.
    if not 1 <= len(location) <= 8192 or any(not 33 <= ord(char) <= 126 for char in location) or "#" in location:
        raise ResponseCaptureError("Unsafe response Location")
    path, separator, query = location.partition("?")
    try:
        _canonical_path(path)
    except IdempotencyValidationError:
        raise ResponseCaptureError("Unsafe response Location") from None
    if separator and (not query or "?" in query):
        raise ResponseCaptureError("Unsafe response Location query")
    return path, query if separator else None


def _relative_query_path(encoded: str, *, max_bytes: int) -> None:
    try:
        value = unquote_to_bytes(encoded).decode("utf-8")
    except UnicodeError:
        raise ResponseCaptureError("Invalid encoded relative path") from None
    # Require one canonical UTF-8 percent-encoding pass, uppercase hex and %20
    # for spaces. Encoded separators become ordinary path separators exactly once.
    if quote(value, safe="") != encoded or not 1 <= len(value.encode("utf-8")) <= max_bytes:
        raise ResponseCaptureError("Invalid encoded relative path")
    if any(char in "\\%?#&=;:" or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise ResponseCaptureError("Unsafe relative path")
    if any(
        not part or part in {".", ".."} or part.casefold() == ".git" or part.endswith((".", " "))
        for part in value.split("/")
    ):
        raise ResponseCaptureError("Unsafe relative path segments")


@dataclass(frozen=True, kw_only=True, slots=True)
class LocationQueryPolicy:
    """Exact destination plus an exact nonempty set of relative-path query keys.

    All declared keys are required; no generic query values or optional wildcards.
    This checks path syntax, not resource ownership or path-content sensitivity.
    """

    path: str
    relative_path_keys: frozenset[str]
    max_path_bytes: int = 1024

    def __post_init__(self) -> None:
        _canonical_path(self.path)
        if type(self.relative_path_keys) is not frozenset or not 1 <= len(self.relative_path_keys) <= 4:
            raise ResponseCaptureError("Expected bounded immutable Location query keys")
        for key in self.relative_path_keys:
            _safe_field(key)
        _bounded_integer(self.max_path_bytes, maximum=2048)

    def validate(self, location: str) -> None:
        path, query = _location_parts(location)
        if path != self.path or query is None:
            raise ResponseCaptureError("Location does not match its exact query policy")
        parts = query.split("&")
        if len(parts) != len(self.relative_path_keys):
            raise ResponseCaptureError("Location has missing, extra or duplicate query keys")
        seen: set[str] = set()
        for part in parts:
            key, separator, value = part.partition("=")
            if not separator or key not in self.relative_path_keys or key in seen or _secret_field(key):
                raise ResponseCaptureError("Location has unapproved or duplicate query keys")
            seen.add(key)
            _relative_query_path(value, max_bytes=self.max_path_bytes)


@dataclass(frozen=True, kw_only=True, slots=True)
class ResponseCapturePolicy:
    """Exact route opt-in; nesting and query Locations require explicit policies.

    Review each scalar field's semantics before enabling it (free-form text could
    itself carry secrets). No redaction is performed. Only Location and explicitly
    opted-in X-Queue-State are supported, never arbitrary semantic headers.
    """

    method: str
    path: str
    allowed_statuses: frozenset[int]
    allowed_json_fields: frozenset[str] = frozenset()
    allow_empty_body: bool = False
    location_prefix: str | None = None
    max_body_bytes: int = 16 * 1024
    body_shape: ResponseShape | None = None
    max_body_depth: int = 32
    max_body_nodes: int = 10_000
    location_query: LocationQueryPolicy | None = None
    queue_state_policy: QueueStatePolicy | None = None

    def __post_init__(self) -> None:
        _route(self.method, self.path)
        if any(segment.casefold() in {"auth", "authentication", "oauth", "oauth2"} for segment in self.path.split("/")):
            raise ResponseCaptureError("Authentication routes must not retain request replays")
        _bounded_integer(self.max_body_bytes, maximum=_MAX_RESPONSE_BYTES)
        _bounded_integer(self.max_body_depth, maximum=64)
        _bounded_integer(self.max_body_nodes, maximum=100_000)
        if type(self.allowed_statuses) is not frozenset or not self.allowed_statuses:
            raise ResponseCaptureError("An explicit immutable status allowlist is required")
        for status in self.allowed_statuses:
            _cacheable_status(status)
        if type(self.allowed_json_fields) is not frozenset or any(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) is None or _secret_field(name)
            for name in self.allowed_json_fields
        ):
            raise ResponseCaptureError("Unsafe response field allowlist")
        if self.location_prefix is not None:
            _canonical_path(self.location_prefix)
        if self.body_shape is not None:
            if self.allowed_json_fields:
                raise ResponseCaptureError("Choose a flat field allowlist or an explicit body shape")
            _validate_shape_configuration(self.body_shape)
        if self.location_query is not None and (
            type(self.location_query) is not LocationQueryPolicy or self.location_prefix is not None
        ):
            raise ResponseCaptureError("Choose a Location prefix or an exact query policy")
        if self.queue_state_policy is not None and (
            type(self.queue_state_policy) is not QueueStatePolicy
            or any(rule.status_code not in self.allowed_statuses for rule in self.queue_state_policy.rules)
        ):
            raise ResponseCaptureError("Queue-state requirements must match approved capture statuses")

    def validate(self, response: CachedResponse) -> None:
        if response.status_code not in self.allowed_statuses or len(response.body) > self.max_body_bytes:
            raise ResponseCaptureError("Response is outside its route capture policy")
        if response.location is not None:
            self._validate_location(response.location)
        if self.queue_state_policy is not None:
            self.queue_state_policy.validate(response)
        elif response.queue_state_header is not None:
            raise ResponseCaptureError("Route is not allowlisted for queue-state capture")
        if not response.body:
            if not self.allow_empty_body:
                raise ResponseCaptureError("Empty response body is not allowlisted")
            return
        self._validate_body(response.body)

    def _validate_body(self, body: bytes) -> None:
        limits = CanonicalJsonLimits(max_input_bytes=self.max_body_bytes)
        try:
            value = _parse_json(body, limits)
            if self.body_shape is None:
                # Preserve the legacy flat policy's independent 256 KiB canonical
                # validation budget. Never replace the original stored body.
                _canonical_json(value, limits)
        except InvalidJsonPayloadError:
            raise ResponseCaptureError("Response is not safe bounded JSON") from None
        if self.body_shape is not None:
            _validate_response_shape(
                value, self.body_shape, max_depth=self.max_body_depth, max_nodes=self.max_body_nodes
            )
        else:
            self._validate_flat_body(value)

    def _validate_flat_body(self, value: _JsonValue) -> None:
        if not isinstance(value, dict):
            raise ResponseCaptureError("Only flat JSON response objects can be retained")
        if len(value) + 1 > self.max_body_nodes:
            raise ResponseCaptureError("Response structure exceeds its bounds")
        scalar = ScalarShape(kinds=frozenset(ScalarKind), max_string_bytes=self.max_body_bytes)
        for key, item in value.items():
            if key not in self.allowed_json_fields or _secret_field(key):
                raise ResponseCaptureError("Response contains non-allowlisted content")
            _validate_response_shape(item, scalar, max_depth=self.max_body_depth, max_nodes=1)

    def _validate_location(self, location: str) -> None:
        if self.location_query is not None:
            self.location_query.validate(location)
            return
        path, query = _location_parts(location)
        if (
            query is not None
            or self.location_prefix is None
            or (path != self.location_prefix and not path.startswith(self.location_prefix.rstrip("/") + "/"))
        ):
            raise ResponseCaptureError("Response Location is outside its route allowlist")


@dataclass(frozen=True, kw_only=True, slots=True)
class RequestRecord:
    id: UUID
    scope: RequestScope
    payload_hash: str = field(repr=False)
    created_at: datetime
    expires_at: datetime
    response: CachedResponse | None = None

    def __post_init__(self) -> None:
        if type(self.id) is not UUID or self.id.int == 0:
            raise IdempotencyValidationError("Expected a reservation UUID")
        if re.fullmatch(r"[0-9a-f]{64}", self.payload_hash) is None:
            raise IdempotencyValidationError("Expected a SHA-256 payload hash")
        if _aware(self.expires_at) - _aware(self.created_at) < MIN_RETENTION:
            raise IdempotencyValidationError("Request-key retention must be at least 24 hours")


class RequestJournal(Protocol):
    """Caller-transaction-bound port. Neither method may commit independently.

    ``reserve`` atomically inserts the candidate, or replaces an existing record
    whose expiry is <= now, otherwise returns the existing live record. Its unique
    key is the ENTIRE scope. It must serialize conflicting concurrent reservations.

    ``complete`` is a single atomic compare-and-set: only the current, live,
    pending reservation with this UUID can complete. Return False for expired,
    replaced, absent or already completed IDs. Never resolve a stale UUID by its
    old scope, overwrite a completed response, or shorten existing retention.
    Store the exact response and extend expiry to at least expires_at. This fence
    protects a replacement reservation, not permanent business-effect deduplication.
    """

    async def reserve(self, candidate: RequestRecord, now: datetime) -> RequestRecord: ...

    async def complete(
        self, record_id: UUID, response: CachedResponse, now: datetime, expires_at: datetime
    ) -> bool: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RequestIdempotency:
    clock: Clock
    response_policies: tuple[ResponseCapturePolicy, ...] = ()
    retention: timedelta = MIN_RETENTION
    json_limits: CanonicalJsonLimits = CanonicalJsonLimits()

    def __post_init__(self) -> None:
        if self.retention < MIN_RETENTION:
            raise IdempotencyValidationError("Request-key retention must be at least 24 hours")
        if type(self.response_policies) is not tuple or len(self.response_policies) > 128:
            raise ResponseCaptureError("Expected a bounded immutable route allowlist")
        routes = {(policy.method, policy.path) for policy in self.response_policies}
        if len(routes) != len(self.response_policies):
            raise ResponseCaptureError("Duplicate route capture policy")

    def _policy(self, scope: RequestScope) -> ResponseCapturePolicy:
        for policy in self.response_policies:
            if (policy.method, policy.path) == (scope.method, scope.path):
                return policy
        raise ResponseCaptureError("Route is not allowlisted for request replay")

    def _expiry(self, now: datetime) -> datetime:
        try:
            return now + self.retention
        except OverflowError:
            raise IdempotencyValidationError("Request-key expiry is outside the clock range") from None

    async def begin(
        self, journal: RequestJournal, scope: RequestScope, raw_json: bytes
    ) -> RequestRecord | CachedResponse:
        """Authorize first. Reserve atomically or replay an exact completed response."""
        policy = self._policy(scope)
        payload_hash = canonical_payload_hash(raw_json, limits=self.json_limits)
        now = _aware(self.clock.now())
        candidate = RequestRecord(
            id=uuid4(), scope=scope, payload_hash=payload_hash, created_at=now, expires_at=self._expiry(now)
        )
        record = await journal.reserve(candidate, now)
        # A later contender may have won while reserve awaited a scope lock.
        # Observe freshness after that await without changing either generation.
        observed_at = _aware(self.clock.now())
        if record.scope != scope or record.created_at > observed_at or record.expires_at <= observed_at:
            raise IdempotencyValidationError("Journal returned an invalid live reservation")
        if record.id == candidate.id:
            if record != candidate:
                raise IdempotencyValidationError("Journal changed a new reservation")
            return record
        if record.payload_hash != payload_hash:
            raise PayloadMismatchError("Request key has a different payload")
        if record.response is None:
            raise RequestInProgressError("Request is still in progress")
        policy.validate(record.response)
        return record.response

    async def complete(
        self,
        journal: RequestJournal,
        record: RequestRecord,
        *,
        status_code: int,
        body: bytes,
        headers: Mapping[str, str] | tuple[tuple[str, str], ...] | None = None,
    ) -> bool:
        """Capture without redaction/commit; False means the completion fence lost.

        Pass the complete set of application-supplied response headers. Transport
        headers may be regenerated by HTTP, but must not hide application cookies,
        authentication, redirects or other semantic headers from this check.
        Use the tuple form for raw header pairs so duplicate names are not lost
        by a mapping conversion. Retain queue-state name casing and value exactly.
        """
        location, queue_state_header = _capture_headers(headers)
        response = CachedResponse(
            status_code=status_code, body=body, location=location, queue_state_header=queue_state_header
        )
        self._policy(record.scope).validate(response)
        now = _aware(self.clock.now())
        if now < record.created_at:
            raise IdempotencyValidationError("Clock precedes the reservation")
        if now >= record.expires_at or record.response is not None:
            return False
        return await journal.complete(record.id, response, now, max(record.expires_at, self._expiry(now)))


def _capture_headers(
    headers: Mapping[str, str] | tuple[tuple[str, str], ...] | None,
) -> tuple[str | None, tuple[str, str] | None]:
    location: str | None = None
    queue_state: tuple[str, str] | None = None
    if headers is None:
        return location, queue_state
    if not isinstance(headers, Mapping) and type(headers) is not tuple:
        raise ResponseCaptureError("Expected immutable raw headers or a header mapping")
    if len(headers) > 2:
        raise ResponseCaptureError("Unsupported or duplicate application headers")
    entries = headers.items() if isinstance(headers, Mapping) else headers
    for entry in entries:
        name, value = _header_pair(entry)
        if name.isascii() and name.lower() == "location" and location is None:
            location = value
        elif name.isascii() and name.lower() == "x-queue-state" and queue_state is None:
            queue_state = (name, value)
        else:
            raise ResponseCaptureError("Unsupported or duplicate application headers")
    return location, queue_state
