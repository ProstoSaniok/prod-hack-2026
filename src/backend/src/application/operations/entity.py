"""Permanent business identities contain references, never request bodies or credentials."""

import re
from dataclasses import dataclass, field
from uuid import UUID

from src.application.common.errors import ConflictError
from src.application.operations.work import Work


def validate_name(value: str) -> None:
    if re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", value) is None:
        raise ValueError("Invalid operation kind or projection topic")


def validate_source(value: str) -> None:
    if not value or len(value) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("Invalid permanent source key")


@dataclass(frozen=True, kw_only=True)
class OperationSpec:
    kind: str
    source_key: str = field(repr=False)
    subject_id: UUID
    payload_hash: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_name(self.kind)
        validate_source(self.source_key)
        if re.fullmatch(r"[0-9a-f]{64}", self.payload_hash) is None:
            raise ValueError("Invalid canonical payload hash")


@dataclass(frozen=True, kw_only=True)
class Operation:
    id: UUID
    spec: OperationSpec
    work: Work
    external_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class ProjectionSpec:
    topic: str
    source_key: str = field(repr=False)
    subject_id: UUID
    revision: int

    def __post_init__(self) -> None:
        validate_name(self.topic)
        validate_source(self.source_key)
        if self.revision < 1:
            raise ValueError("Projection revisions start at one")


@dataclass(frozen=True, kw_only=True)
class OutboxMessage:
    id: UUID
    spec: ProjectionSpec
    work: Work


class SourceConflictError(ConflictError):
    """A permanent source cannot be rebound to different business inputs."""
