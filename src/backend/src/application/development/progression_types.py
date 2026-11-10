"""Immutable policy inputs and explanations, not development persistence entities."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from src.application.common.errors import ValidationError

PROGRESSION_POLICY_VERSION = "p02-v1"


class ProgressionValidationError(ValidationError):
    """An input is invalid or cannot establish a current, consistent snapshot."""


class ProgressionConflictError(ProgressionValidationError):
    """A business source already has different evidence; no implicit replacement."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContributionKey:
    participant_id: UUID
    case_id: UUID
    skill_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class AssessorSnapshot:
    """Server-validated historical UserCompetency value in exactly one skill.

    ``version`` is the snapshotted UserCompetency.version, never a grant version
    or a synthetic packing of multiple versions. Persist grant ID/version as
    separate provenance at the caller's persistence boundary. ``confirmed_expert``
    records a historical fact; current grant/rights validation remains the
    finalization caller's responsibility and cannot be bypassed by this snapshot.
    """

    assessor_id: UUID
    skill_id: UUID
    version: int
    expertise: Decimal
    confirmed_expert: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityConfirmationSnapshot:
    """Assessment versions are distinct from expert UserCompetency.version."""

    assessment_id: UUID
    version: int
    expected_version: int
    source_id: UUID
    source_version: int
    case_version: int
    policy_version: str
    decision: Literal["confirmed", "needs_revision"]
    quality_score: Decimal
    expert: AssessorSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class SolutionProgressionInput:
    """One skill of an immutable solution-review revision.

    All numeric inputs must be finite Decimal instances; no float coercion is
    performed. Expected versions come from the caller's authoritative state.
    Supervision is the original assignment snapshot, never derived from a
    reviewer's later graduation. Quality and solution scores are distinct.
    """

    source_id: UUID
    source_version: int
    expected_source_version: int
    participant_id: UUID
    case_id: UUID
    case_version: int
    expected_case_version: int
    policy_version: str
    skill_id: UUID
    solution_score: Decimal
    weight: Decimal
    verdict: Literal["approved", "changes_requested"]
    finalized: bool
    supervision_required: bool
    reviewer: AssessorSnapshot
    confirmation: QualityConfirmationSnapshot | None = None

    @property
    def key(self) -> ContributionKey:
        return ContributionKey(participant_id=self.participant_id, case_id=self.case_id, skill_id=self.skill_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class SolutionContribution:
    source: SolutionProgressionInput
    assessor: AssessorSnapshot
    amount: Decimal

    @property
    def key(self) -> ContributionKey:
        return self.source.key


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillProgression:
    participant_id: UUID
    skill_id: UUID
    contributions: tuple[SolutionContribution, ...]
    uncapped_total: Decimal
    capped_value: Decimal
    displayed_value: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressionEffect:
    before: SkillProgression
    after: SkillProgression
    contribution: SolutionContribution | None
    applied: bool
