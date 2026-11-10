"""Explicit expert readiness; ordinary grade entry belongs to publication effects."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from src.application.catalog.management import CaseValidationError
from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.user.entity import UserId

Decision = Literal["approved", "needs_development"]


@dataclass(frozen=True, kw_only=True)
class ApplicationInput:
    competency_id: UUID
    expected_user_competency_version: int

    def __post_init__(self) -> None:
        require_version(self.expected_user_competency_version)


def require_version(version: int) -> None:
    if type(version) is not int or version < 1:
        raise CaseValidationError("Expected version must be a positive integer")


@dataclass(frozen=True, kw_only=True)
class AssessmentInput:
    expected_application_version: int
    expected_user_competency_version: int
    decision: Decision
    feedback: str

    def __post_init__(self) -> None:
        require_version(self.expected_application_version)
        require_version(self.expected_user_competency_version)
        if self.decision not in {"approved", "needs_development"}:
            raise CaseValidationError("Invalid readiness decision")
        if not self.feedback.strip() or len(self.feedback) > 6000 or "\x00" in self.feedback:
            raise CaseValidationError("Actionable feedback of at most 6000 characters is required")
        try:
            self.feedback.encode("utf-8")
        except UnicodeError:
            raise CaseValidationError("Feedback must be UTF-8 text") from None


class DevelopmentStore(Protocol):
    async def apply(self, actor: UserId, command: ApplicationInput, key: str) -> CachedResponse: ...
    async def assess(
        self, actor: UserId, application_id: UUID, command: AssessmentInput, key: str
    ) -> CachedResponse: ...
    async def applications(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def application(self, actor: UserId, application_id: UUID) -> bytes: ...
    async def assessment(self, actor: UserId, application_id: UUID, assessment_id: UUID) -> bytes: ...
    async def inbox(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def evidence_review(self, actor: UserId, application_id: UUID, review_id: UUID) -> bytes: ...


class DevelopmentQueries:
    def __init__(self, identity: IdentityProvider, store: DevelopmentStore) -> None:
        self.identity, self.store = identity, store

    async def applications(self, *, limit: int, offset: int) -> bytes:
        return await self.store.applications((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def application(self, application_id: UUID) -> bytes:
        return await self.store.application((await self.identity.get_current_user()).id, application_id)

    async def assessment(self, application_id: UUID, assessment_id: UUID) -> bytes:
        return await self.store.assessment((await self.identity.get_current_user()).id, application_id, assessment_id)

    async def inbox(self, *, limit: int, offset: int) -> bytes:
        return await self.store.inbox((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def evidence_review(self, application_id: UUID, review_id: UUID) -> bytes:
        return await self.store.evidence_review((await self.identity.get_current_user()).id, application_id, review_id)
