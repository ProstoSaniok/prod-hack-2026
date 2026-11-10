"""Expert quality decisions, immutable revision reads and owner probation views."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.catalog.management import CaseValidationError
from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.review.entity import QualityDecision
from src.application.user.entity import UserId


def _feedback(value: str) -> None:
    if not isinstance(value, str):
        raise CaseValidationError("Quality feedback must be nonblank and at most 6000 characters")
    if not value.strip() or len(value) > 6000:
        raise CaseValidationError("Quality feedback must be nonblank and at most 6000 characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise CaseValidationError("Quality feedback must be UTF-8 text") from None
    if "\x00" in value:
        raise CaseValidationError("Quality feedback cannot contain NUL")


@dataclass(frozen=True, kw_only=True)
class QualityInput:
    assignment_id: UUID
    revision_id: UUID
    decision: QualityDecision
    score: Decimal
    feedback: str

    def __post_init__(self) -> None:
        if not isinstance(self.assignment_id, UUID):
            raise CaseValidationError("Quality assessment requires assignment and revision identities")
        if not isinstance(self.revision_id, UUID):
            raise CaseValidationError("Quality assessment requires assignment and revision identities")
        if not isinstance(self.decision, QualityDecision):
            raise CaseValidationError("Invalid quality decision")
        if not isinstance(self.score, Decimal):
            raise CaseValidationError("Quality score must be between 0 and 100")
        if not self.score.is_finite() or not 0 <= self.score <= 100:
            raise CaseValidationError("Quality score must be between 0 and 100")
        if self.decision == QualityDecision.CONFIRMED and self.score < 70:
            raise CaseValidationError("Confirmation requires quality at least 70")
        _feedback(self.feedback)


class ReviewQualityStore(Protocol):
    async def assess(self, actor: UserId, review_id: UUID, command: QualityInput, key: str) -> CachedResponse: ...

    async def revise(
        self, actor: UserId, review_id: UUID, expected_revision_id: UUID, expected_draft_version: int, key: str
    ) -> CachedResponse: ...


class QualityReader(Protocol):
    async def source(self, actor: UserId, assignment_id: UUID) -> bytes: ...
    async def assessment(self, actor: UserId, review_id: UUID, assessment_id: UUID) -> bytes: ...
    async def assessments(self, actor: UserId, review_id: UUID, *, limit: int, offset: int) -> bytes: ...
    async def revision(self, actor: UserId, review_id: UUID, revision_id: UUID) -> bytes: ...
    async def probation(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...


class ReviewQualityQueries:
    def __init__(self, identity: IdentityProvider, reader: QualityReader) -> None:
        self.identity, self.reader = identity, reader

    async def source(self, assignment_id: UUID) -> bytes:
        return await self.reader.source((await self.identity.get_current_user()).id, assignment_id)

    async def assessment(self, review_id: UUID, assessment_id: UUID) -> bytes:
        return await self.reader.assessment((await self.identity.get_current_user()).id, review_id, assessment_id)

    async def assessments(self, review_id: UUID, *, limit: int, offset: int) -> bytes:
        return await self.reader.assessments(
            (await self.identity.get_current_user()).id, review_id, limit=limit, offset=offset
        )

    async def revision(self, review_id: UUID, revision_id: UUID) -> bytes:
        return await self.reader.revision((await self.identity.get_current_user()).id, review_id, revision_id)

    async def probation(self, *, limit: int, offset: int) -> bytes:
        return await self.reader.probation((await self.identity.get_current_user()).id, limit=limit, offset=offset)
