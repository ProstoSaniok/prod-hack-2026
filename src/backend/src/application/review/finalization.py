"""Trusted publication facts and transactional effects, never client-supplied proofs."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.catalog.management import CaseValidationError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.review.entity import ReviewVerdict, RevisionContent
from src.application.user.entity import UserId


def require_final_content(content: RevisionContent) -> None:
    if (content.verdict == ReviewVerdict.APPROVED) != (content.score >= 70):
        raise CaseValidationError("Approved requires score at least 70; lower scores require changes_requested")
    if any(not item.strip() or len(item) > 6000 for item in (content.strengths, content.problems, content.next_steps)):
        raise CaseValidationError("Final feedback must describe strengths, problems and next steps")


@dataclass(frozen=True, kw_only=True)
class PublicationSkill:
    competency_id: UUID
    weight: Decimal
    reviewer_value: Decimal
    reviewer_grade: SkillGrade
    grant_id: UUID
    grant_version: int
    competency_version: int
    grant_state: SkillGrantState


@dataclass(frozen=True, kw_only=True)
class PublicationConfirmation:
    assessment_id: UUID
    expert_id: UserId
    quality_score: Decimal
    skills: tuple[PublicationSkill, ...]


@dataclass(frozen=True, kw_only=True)
class ReviewPublication:
    review_id: UUID
    revision_id: UUID
    submission_id: UUID
    case_id: UUID
    case_version: int
    author_id: UserId
    reviewer_id: UserId
    content: RevisionContent
    skills: tuple[PublicationSkill, ...]
    created_at: datetime
    supervision_required: bool
    revision_number: int = 1
    confirmation: PublicationConfirmation | None = None


class PublicationEffects(Protocol):
    async def apply(self, publication: ReviewPublication) -> None:
        """Stage real progression/rewards/outbox in the caller's UoW; never commit."""
        ...
