"""Immutable, server-derived policy inputs, not persistence entities or HTTP models."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from src.application.common.errors import ValidationError

POLICY_VERSION = "prodguild-development-v1"


class PolicyViolationError(ValidationError):
    """A development policy precondition is not satisfied."""


class Grade(StrEnum):
    PERFORMER = "performer"
    REVIEW_MINUS = "review_minus"
    REVIEW_PLUS = "review_plus"
    EXPERT = "expert"


class Grant(StrEnum):
    NONE = "none"
    SUPERVISED = "supervised"
    CONFIRMED = "confirmed"
    REVOKED = "revoked"


class QualityDecision(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_REVISION = "needs_revision"


class ReadinessDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


def validate_enum(value: object, enum_type: type[StrEnum]) -> None:
    if not isinstance(value, enum_type):
        raise PolicyViolationError("Unknown policy enum value")


def validate_value(value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or not Decimal(0) <= value <= Decimal(100):
        raise PolicyViolationError("A finite Decimal in [0, 100] is required")


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillAuthorityInput:
    user_id: UUID
    skill_id: UUID
    value: Decimal
    grade: Grade
    grant: Grant
    expert_confirmed: bool = False

    def __post_init__(self) -> None:
        validate_value(self.value)
        validate_enum(self.grade, Grade)
        validate_enum(self.grant, Grant)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseSkillInput:
    skill_id: UUID
    required_value: Decimal
    author_grade: Grade

    def __post_init__(self) -> None:
        validate_value(self.required_value)
        validate_enum(self.author_grade, Grade)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbationBinding:
    probation_id: UUID
    skill_id: UUID
    grade: Grade

    def __post_init__(self) -> None:
        validate_enum(self.grade, Grade)
        if self.grade not in {Grade.REVIEW_MINUS, Grade.REVIEW_PLUS}:
            raise PolicyViolationError("Only non-expert reviewer grades have probation")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewSnapshot:
    review_id: UUID
    revision_id: UUID
    author_id: UUID
    reviewer_id: UUID
    skill_ids: frozenset[UUID]
    probation_bindings: tuple[ProbationBinding, ...] = ()

    def __post_init__(self) -> None:
        binding_skills = [binding.skill_id for binding in self.probation_bindings]
        if (
            not self.skill_ids
            or self.author_id == self.reviewer_id
            or len(binding_skills) != len(set(binding_skills))
            or not set(binding_skills) <= self.skill_ids
        ):
            raise PolicyViolationError("Invalid review or supervision snapshot")


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityAssignmentInput:
    assignment_id: UUID
    review_id: UUID
    revision_id: UUID
    expert_id: UUID
    active: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityAssessmentInput:
    assignment: QualityAssignmentInput
    decision: QualityDecision
    quality_score: Decimal
    feedback: str

    def __post_init__(self) -> None:
        validate_value(self.quality_score)
        validate_enum(self.decision, QualityDecision)
        if not self.feedback.strip():
            raise PolicyViolationError("A known decision and actionable feedback are required")


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityEvidence:
    """Historical output from assess_review_quality; map only trusted persisted evidence."""

    review: ReviewSnapshot
    assignment_id: UUID
    expert_id: UUID
    expert_snapshots: tuple[SkillAuthorityInput, ...]
    decision: QualityDecision
    quality_score: Decimal
    feedback: str
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        validate_value(self.quality_score)
        validate_enum(self.decision, QualityDecision)
        if not self.feedback.strip() or (self.decision == QualityDecision.CONFIRMED and self.quality_score < 70):
            raise PolicyViolationError("Invalid historical quality evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbationSnapshot:
    probation_id: UUID
    user_id: UUID
    skill_id: UUID
    grade: Grade
    confirmed_review_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        validate_enum(self.grade, Grade)
        if self.grade not in {Grade.REVIEW_MINUS, Grade.REVIEW_PLUS} or len(self.confirmed_review_ids) > 3:
            raise PolicyViolationError("Invalid probation grade or distinct-review evidence")

    @property
    def passed(self) -> bool:
        return len(self.confirmed_review_ids) == 3


@dataclass(frozen=True, slots=True, kw_only=True)
class SuccessfulCaseInput:
    participant_id: UUID
    case_id: UUID
    skill_id: UUID
    solution_score: Decimal
    approved: bool
    finalized: bool

    def __post_init__(self) -> None:
        validate_value(self.solution_score)


@dataclass(frozen=True, slots=True, kw_only=True)
class GradeEvidenceInput:
    successful_cases: tuple[SuccessfulCaseInput, ...] = ()
    quality_assessments: tuple[QualityEvidence, ...] = ()
    probations: tuple[ProbationSnapshot, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class GradeGateEvidence:
    user_id: UUID
    skill_id: UUID
    target_grade: Grade
    value: Decimal
    successful_case_ids: frozenset[UUID]
    good_review_ids: frozenset[UUID]
    passed_probation_ids: frozenset[UUID]
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessRequest:
    application_id: UUID
    evidence: GradeGateEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessAssessment:
    """Decision proposal; does not itself grant rights or commit a transaction."""

    request: ReadinessRequest
    assessor: SkillAuthorityInput
    decision: ReadinessDecision
    feedback: str
    current_evidence: GradeGateEvidence | None
    policy_version: str = POLICY_VERSION
