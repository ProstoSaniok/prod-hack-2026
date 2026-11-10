from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.common.entity import Entity, to_entity
from src.application.user.entity import User, UserId

UserCompetencyId = NewType("UserCompetencyId", UUID)
SkillGrantId = NewType("SkillGrantId", UUID)
ProbationId = NewType("ProbationId", UUID)
ProbationSuccessId = NewType("ProbationSuccessId", UUID)
ReviewerApplicationId = NewType("ReviewerApplicationId", UUID)
ReviewerApplicationAssessmentId = NewType("ReviewerApplicationAssessmentId", UUID)
ProgressionContributionId = NewType("ProgressionContributionId", UUID)


class SkillGrade(StrEnum):
    PERFORMER = "performer"
    REVIEW_MINUS = "review_minus"
    REVIEW_PLUS = "review_plus"
    EXPERT = "expert"


class SkillGrantState(StrEnum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    REVOKED = "revoked"


class SkillGrantEvidenceKind(StrEnum):
    PROGRESSION = "progression"
    INDEPENDENT_READINESS = "independent_readiness"
    SEED = "seed"


class ReviewerApplicationState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ReadinessDecision(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"


def _validate_decimal(
    name: str,
    value: Decimal,
    *,
    minimum: Decimal = Decimal(0),
    maximum: Decimal | None = None,
    exclusive_minimum: bool = False,
) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < minimum or (exclusive_minimum and value == minimum):
        raise ValueError(f"{name} is below its lower bound")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} is above its upper bound")


def _validate_version(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be positive")


@to_entity
class UserCompetency(Entity[UserCompetencyId]):
    """Current per-skill aggregate; unique by (user_id, competency_id)."""

    user_id: UserId
    competency_id: UUID
    contribution_sum: Decimal = Decimal(0)
    displayed_value: Decimal = Decimal(0)
    grade: SkillGrade = SkillGrade.PERFORMER
    version: int = 1

    def __post_init__(self) -> None:
        _validate_decimal("contribution_sum", self.contribution_sum)
        _validate_decimal("displayed_value", self.displayed_value, maximum=Decimal(100))
        _validate_version("version", self.version)


@to_entity
class SkillGrant(Entity[SkillGrantId]):
    """Historical grant; superseded grants must be revoked, not overwritten."""

    user_id: UserId
    competency_id: UUID
    user_competency_version: int
    grade: SkillGrade
    state: SkillGrantState
    evidence_kind: SkillGrantEvidenceKind
    evidence: str
    version: int = 1
    readiness_assessment_id: ReviewerApplicationAssessmentId | None = None
    confirmed_by_user_id: UserId | None = None
    confirmed_by_grant_id: SkillGrantId | None = None
    confirmed_by_grant_version: int | None = None
    confirmed_at: datetime | None = None
    seed_reference: str | None = None
    revoked_at: datetime | None = None
    readiness_decision: ReadinessDecision = ReadinessDecision.CONFIRMED
    confirmed_by_grade: SkillGrade = SkillGrade.EXPERT

    def __post_init__(self) -> None:
        _validate_version("version", self.version)
        _validate_version("user_competency_version", self.user_competency_version)
        if not self.evidence.strip():
            raise ValueError("Grant evidence is required")
        if self.grade == SkillGrade.PERFORMER:
            raise ValueError("Performer has no review grant")
        if self.confirmed_by_user_id == self.user_id:
            raise ValueError("A user cannot confirm their own readiness")
        if self.confirmed_by_grant_version is not None:
            _validate_version("confirmed_by_grant_version", self.confirmed_by_grant_version)
        if (self.state == SkillGrantState.REVOKED) != (self.revoked_at is not None):
            raise ValueError("Revoked state and revoked_at must agree")
        if self.state == SkillGrantState.CONFIRMED and self.confirmed_at is None:
            raise ValueError("Confirmed grants require confirmed_at")
        if self.evidence_kind == SkillGrantEvidenceKind.SEED and not (
            self.seed_reference and self.seed_reference.strip()
        ):
            raise ValueError("Seed evidence requires an explicit seed reference")
        if self.evidence_kind == SkillGrantEvidenceKind.SEED and self.readiness_assessment_id is not None:
            raise ValueError("Seed evidence is separate from independent readiness")
        self._validate_confirmation_evidence()

    def _validate_confirmation_evidence(self) -> None:
        if self.readiness_decision != ReadinessDecision.CONFIRMED or self.confirmed_by_grade != SkillGrade.EXPERT:
            raise ValueError("Readiness evidence requires a confirming expert decision")
        if self.grade == SkillGrade.EXPERT and self.confirmed_at is not None and not self.has_expert_evidence():
            raise ValueError("Expert confirmation requires independent readiness or explicit seed evidence")

    def has_expert_evidence(self) -> bool:
        """Historical provenance only; never a substitute for current rights."""
        if not self.evidence.strip():
            return False
        if self.evidence_kind == SkillGrantEvidenceKind.SEED:
            return bool(self.seed_reference and self.seed_reference.strip()) and self.readiness_assessment_id is None
        return (
            self.evidence_kind == SkillGrantEvidenceKind.INDEPENDENT_READINESS
            and self.readiness_assessment_id is not None
            and self.confirmed_by_user_id is not None
            and self.confirmed_by_user_id != self.user_id
            and self.confirmed_by_grant_id is not None
            and self.confirmed_by_grant_id != self.id
            and self.confirmed_by_grant_version is not None
            and self.confirmed_by_grant_version > 0
        )


@dataclass(frozen=True, kw_only=True)
class SkillSnapshot:
    """Historical explanation, deliberately not accepted by rights checks."""

    user_id: UserId
    competency_id: UUID
    user_competency_version: int
    value: Decimal
    grade: SkillGrade
    grant_id: SkillGrantId | None = None
    grant_version: int | None = None
    grant_state: SkillGrantState | None = None

    def __post_init__(self) -> None:
        _validate_decimal("value", self.value, maximum=Decimal(100))
        _validate_version("user_competency_version", self.user_competency_version)
        if self.grant_version is not None:
            _validate_version("grant_version", self.grant_version)
        grant_fields_present = (self.grant_id is not None, self.grant_version is not None, self.grant_state is not None)
        if any(grant_fields_present) and not all(grant_fields_present):
            raise ValueError("Snapshot grant identity, version and state must be supplied together")


@to_entity
class Probation(Entity[ProbationId]):
    """Unique (user_id, competency_id, grade, epoch); successes are separate rows."""

    user_id: UserId
    competency_id: UUID
    grade: SkillGrade
    skill_grant_id: SkillGrantId
    epoch: int
    required_successes: int = 3
    completed_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if self.grade not in {SkillGrade.REVIEW_MINUS, SkillGrade.REVIEW_PLUS}:
            raise ValueError("Only non-expert reviewer grades have probation")
        _validate_version("epoch", self.epoch)
        _validate_version("version", self.version)
        if self.required_successes != 3:
            raise ValueError("Probation requires three distinct reviews")


@to_entity
class ProbationSuccess(Entity[ProbationSuccessId]):
    """Deduplicate by (probation_id, review_id), never by revision/assessment."""

    probation_id: ProbationId
    reviewer_id: UserId
    competency_id: UUID
    review_id: UUID
    review_revision_id: UUID
    quality_assessment_id: UUID
    expert_user_id: UserId
    expert_grant_id: SkillGrantId
    expert_grant_version: int
    quality_score: Decimal
    quality_decision: ReadinessDecision = ReadinessDecision.CONFIRMED

    def __post_init__(self) -> None:
        if self.reviewer_id == self.expert_user_id:
            raise ValueError("Reviewers cannot confirm their own probation success")
        if self.quality_decision != ReadinessDecision.CONFIRMED:
            raise ValueError("Probation successes require confirmed quality")
        _validate_version("expert_grant_version", self.expert_grant_version)
        _validate_decimal("quality_score", self.quality_score, minimum=Decimal(70), maximum=Decimal(100))


@to_entity
class ReviewerApplication(Entity[ReviewerApplicationId]):
    user_id: UserId
    competency_id: UUID
    requested_grade: SkillGrade
    user_competency_version: int
    evidence: str
    state: ReviewerApplicationState = ReviewerApplicationState.PENDING
    version: int = 1

    def __post_init__(self) -> None:
        if self.requested_grade == SkillGrade.PERFORMER:
            raise ValueError("Readiness applications request a reviewer grade")
        if not self.evidence.strip():
            raise ValueError("Readiness evidence is required")
        _validate_version("user_competency_version", self.user_competency_version)
        _validate_version("version", self.version)


@to_entity
class ReviewerApplicationAssessment(Entity[ReviewerApplicationAssessmentId]):
    """Immutable decision evidence for a precise application version and skill."""

    application_id: ReviewerApplicationId
    application_version: int
    applicant_id: UserId
    competency_id: UUID
    requested_grade: SkillGrade
    expert_user_id: UserId
    expert_grant_id: SkillGrantId
    expert_grant_version: int
    expert_competency_version: int
    expert_value: Decimal
    decision: ReadinessDecision
    feedback: str
    expert_grade: SkillGrade = SkillGrade.EXPERT
    expert_grant_state: SkillGrantState = SkillGrantState.CONFIRMED

    def __post_init__(self) -> None:
        if self.applicant_id == self.expert_user_id:
            raise ValueError("A user cannot assess their own readiness")
        if self.expert_grade != SkillGrade.EXPERT or self.expert_grant_state != SkillGrantState.CONFIRMED:
            raise ValueError("Readiness assessors need confirmed per-skill expert rights")
        if self.requested_grade == SkillGrade.PERFORMER:
            raise ValueError("Readiness assessments target a reviewer grade")
        if not self.feedback.strip():
            raise ValueError("Readiness feedback is required")
        _validate_version("application_version", self.application_version)
        _validate_version("expert_grant_version", self.expert_grant_version)
        _validate_version("expert_competency_version", self.expert_competency_version)
        _validate_decimal("expert_value", self.expert_value, minimum=Decimal(75), maximum=Decimal(100))


@to_entity
class ProgressionContribution(Entity[ProgressionContributionId]):
    """One effective (participant_id, case_id, competency_id), retaining old sources.

    source_id identifies the authoritative finalization result, not an HTTP request.
    source_contribution_id links a later correction to the contribution it replaces.
    Values are stored verbatim; formula, rounding and finalization belong to policies.
    """

    participant_id: UserId
    case_id: UUID
    competency_id: UUID
    review_id: UUID
    review_revision_id: UUID
    source_id: UUID
    policy_version: str
    case_version: int
    score: Decimal
    weight: Decimal
    assessor_id: UserId
    assessor_grant_id: SkillGrantId
    assessor_grant_version: int
    assessor_competency_version: int
    expertise_value: Decimal
    contribution: Decimal
    contribution_sum_before: Decimal
    contribution_sum_after: Decimal
    displayed_value_before: Decimal
    displayed_value_after: Decimal
    effective: bool = True
    source_contribution_id: ProgressionContributionId | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version is required")
        _validate_version("case_version", self.case_version)
        _validate_version("assessor_grant_version", self.assessor_grant_version)
        _validate_version("assessor_competency_version", self.assessor_competency_version)
        _validate_version("version", self.version)
        _validate_decimal("score", self.score, maximum=Decimal(100))
        _validate_decimal("weight", self.weight, maximum=Decimal("0.25"), exclusive_minimum=True)
        _validate_decimal("expertise_value", self.expertise_value, maximum=Decimal(100))
        _validate_decimal("contribution", self.contribution)
        _validate_decimal("contribution_sum_before", self.contribution_sum_before)
        _validate_decimal("contribution_sum_after", self.contribution_sum_after)
        _validate_decimal("displayed_value_before", self.displayed_value_before, maximum=Decimal(100))
        _validate_decimal("displayed_value_after", self.displayed_value_after, maximum=Decimal(100))


def has_review_rights(
    user: User,
    competency_id: UUID,
    competency: UserCompetency,
    grant: SkillGrant,
    *,
    supervised: bool = False,
) -> bool:
    """Check freshly loaded per-skill records, not case matching or promotion gates.

    Callers must lock/reload these records in their UoW at claim and completion.
    This predicate performs no I/O; an old ORM instance is not live authorization.
    """
    if (
        user.deleted_at is not None
        or competency.deleted_at is not None
        or grant.deleted_at is not None
        or grant.revoked_at is not None
        or competency.user_id != user.id
        or grant.user_id != user.id
        or competency.competency_id != competency_id
        or grant.competency_id != competency_id
        or competency.grade != grant.grade
        or grant.grade == SkillGrade.PERFORMER
    ):
        return False
    if grant.grade == SkillGrade.EXPERT:
        return (
            grant.state == SkillGrantState.CONFIRMED and grant.confirmed_at is not None and grant.has_expert_evidence()
        )
    return (grant.state == SkillGrantState.CONFIRMED and grant.confirmed_at is not None) or (
        supervised and grant.state == SkillGrantState.PROVISIONAL
    )


def has_expert_rights(user: User, competency_id: UUID, competency: UserCompetency, grant: SkillGrant) -> bool:
    return grant.grade == SkillGrade.EXPERT and has_review_rights(user, competency_id, competency, grant)
