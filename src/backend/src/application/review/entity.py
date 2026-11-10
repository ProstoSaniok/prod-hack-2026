from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.catalog.entity import CompetencyId
from src.application.common.entity import Entity, to_entity
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.project.entity import SubmissionId
from src.application.user.entity import UserId

ReviewJobId = NewType("ReviewJobId", UUID)
ReviewAssignmentId = NewType("ReviewAssignmentId", UUID)
ReviewDraftId = NewType("ReviewDraftId", UUID)
ReviewId = NewType("ReviewId", UUID)
ReviewRevisionId = NewType("ReviewRevisionId", UUID)
ReviewCommentId = NewType("ReviewCommentId", UUID)
AssignmentCompetencyId = NewType("AssignmentCompetencyId", UUID)
ReviewerSnapshotId = NewType("ReviewerSnapshotId", UUID)
QualityAssessmentId = NewType("QualityAssessmentId", UUID)
AssessorSnapshotId = NewType("AssessorSnapshotId", UUID)


class ReviewKind(StrEnum):
    REVIEW = "review"
    QUALITY_CHECK = "quality_check"
    RECHECK = "recheck"


class JobState(StrEnum):
    WAITING = "waiting"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssignmentState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ReviewState(StrEnum):
    PENDING = "pending"
    AWAITING_QUALITY = "awaiting_quality"
    NEEDS_REVISION = "needs_revision"
    PUBLISHED = "published"


class ReviewVerdict(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class QualityDecision(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_REVISION = "needs_revision"


class DiffSide(StrEnum):
    OLD = "old"
    NEW = "new"


@to_entity
class ReviewJob(Entity[ReviewJobId]):
    submission_id: SubmissionId
    kind: ReviewKind
    policy_version: str
    reviewed_revision_id: ReviewRevisionId | None = None
    state: JobState = JobState.WAITING

    def __post_init__(self) -> None:
        if (self.kind == ReviewKind.QUALITY_CHECK) != (self.reviewed_revision_id is not None):
            raise ValueError("Only quality jobs target an exact review revision")


@to_entity
class ReviewAssignment(Entity[ReviewAssignmentId]):
    job_id: ReviewJobId
    submission_id: SubmissionId
    kind: ReviewKind
    reviewer_id: UserId
    lease_expires_at: datetime | None
    supervised: bool
    state: AssignmentState = AssignmentState.ACTIVE

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None or (
            self.lease_expires_at is not None and self.lease_expires_at.utcoffset() is None
        ):
            raise ValueError("Assignment lease timestamps must be timezone-aware")
        if self.lease_expires_at is not None and self.lease_expires_at <= self.created_at:
            raise ValueError("Assignment lease must end after it starts")


@to_entity
class AssignmentCompetency(Entity[AssignmentCompetencyId]):
    assignment_id: ReviewAssignmentId
    competency_id: CompetencyId
    grant_id: UUID
    grant_version: int
    user_competency_version: int
    value: Decimal
    grade: SkillGrade
    grant_state: SkillGrantState
    probation_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.user_competency_version < 1:
            raise ValueError("Snapshotted competency version must be positive")


@to_entity
class ReviewDraft(Entity[ReviewDraftId]):
    assignment_id: ReviewAssignmentId
    version: int = 1
    score: Decimal | None = None
    verdict: ReviewVerdict | None = None
    strengths: str = ""
    problems: str = ""
    next_steps: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class RevisionContent:
    score: Decimal
    verdict: ReviewVerdict
    strengths: str
    problems: str
    next_steps: str

    def __post_init__(self) -> None:
        if not self.score.is_finite() or not 0 <= self.score <= 100:
            raise ValueError("Review score must be between 0 and 100")
        if not all(text.strip() for text in (self.strengths, self.problems, self.next_steps)):
            raise ValueError("Final feedback must cover strengths, problems and next steps")


@to_entity
class ReviewResult(Entity[ReviewId]):
    assignment_id: ReviewAssignmentId
    submission_id: SubmissionId
    reviewer_id: UserId
    kind: ReviewKind = ReviewKind.REVIEW
    state: ReviewState = ReviewState.PENDING
    published_at: datetime | None = None
    published_revision_id: ReviewRevisionId | None = None


@to_entity
class ReviewRevision(Entity[ReviewRevisionId]):
    review_id: ReviewId
    submission_id: SubmissionId
    number: int
    policy_version: str
    score: Decimal
    verdict: ReviewVerdict
    strengths: str
    problems: str
    next_steps: str
    source_draft_version: int | None = None

    @property
    def content(self) -> RevisionContent:
        return RevisionContent(
            score=self.score,
            verdict=self.verdict,
            strengths=self.strengths,
            problems=self.problems,
            next_steps=self.next_steps,
        )

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("Revision numbers start at one")
        _ = self.content


@to_entity
class ReviewComment(Entity[ReviewCommentId]):
    text: str
    draft_id: ReviewDraftId | None = None
    revision_id: ReviewRevisionId | None = None
    path: str | None = None
    line: int | None = None
    end_line: int | None = None
    side: DiffSide | None = None


@to_entity
class ReviewerSnapshot(Entity[ReviewerSnapshotId]):
    revision_id: ReviewRevisionId
    competency_id: CompetencyId
    value: Decimal
    grade: SkillGrade
    grant_id: UUID
    grant_version: int
    user_competency_version: int
    grant_state: SkillGrantState

    def __post_init__(self) -> None:
        if self.user_competency_version < 1:
            raise ValueError("Snapshotted competency version must be positive")


@to_entity
class QualityAssessment(Entity[QualityAssessmentId]):
    assignment_id: ReviewAssignmentId
    job_id: ReviewJobId
    submission_id: SubmissionId
    revision_id: ReviewRevisionId
    assessor_id: UserId
    quality_score: Decimal
    decision: QualityDecision
    feedback: str
    kind: ReviewKind = ReviewKind.QUALITY_CHECK

    def __post_init__(self) -> None:
        if not self.quality_score.is_finite() or not 0 <= self.quality_score <= 100:
            raise ValueError("Review quality must be between 0 and 100")
        if self.decision == QualityDecision.CONFIRMED and self.quality_score < 70:
            raise ValueError("Confirmation requires quality at least 70")
        if not self.feedback.strip():
            raise ValueError("Quality assessment requires actionable feedback")


@to_entity
class AssessorSnapshot(Entity[AssessorSnapshotId]):
    assessment_id: QualityAssessmentId
    competency_id: CompetencyId
    value: Decimal
    grant_id: UUID
    grant_version: int
    user_competency_version: int
    grade: SkillGrade = SkillGrade.EXPERT
    grant_state: SkillGrantState = SkillGrantState.CONFIRMED

    def __post_init__(self) -> None:
        if self.user_competency_version < 1:
            raise ValueError("Snapshotted competency version must be positive")
