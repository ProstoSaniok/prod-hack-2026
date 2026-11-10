from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.common.errors import NotFoundError
from src.application.common.identity_provider import IdentityProvider
from src.application.development.entity import SkillGrade
from src.application.development.policy_types import POLICY_VERSION, Grant
from src.application.development.progression_types import PROGRESSION_POLICY_VERSION
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserId, UserRole
from src.application.wallet.policy import PRIORITY_COST


class ProfileNotFoundError(NotFoundError):
    pass


@dataclass(frozen=True, kw_only=True)
class GrantEvidence:
    grant_id: UUID
    grant_version: int
    user_competency_version: int
    readiness_assessment_id: UUID | None
    confirmed_by_grant_id: UUID | None
    confirmed_by_grant_version: int | None


@dataclass(frozen=True, kw_only=True)
class SkillProbation:
    id: UUID
    competency_id: UUID
    grade: SkillGrade
    grant_id: UUID
    version: int
    epoch: int
    confirmed_reviews: int
    remaining: int
    passed: bool
    completed_at: datetime | None
    required_successes: int = 3


@dataclass(frozen=True, kw_only=True)
class ExpertApplicationEligibility:
    can_apply: bool
    pending_application_id: UUID | None


@dataclass(frozen=True, kw_only=True)
class NextGrade:
    grade: SkillGrade
    xp_remaining: Decimal
    successful_cases: int
    previous_probation_passed: bool


@dataclass(frozen=True, kw_only=True)
class ProfileCompetency:
    competency_id: UUID
    name: str
    contribution_sum: Decimal
    value: Decimal
    grade: SkillGrade
    version: int
    grant: Grant
    review_allowed: bool
    expert_confirmed: bool
    evidence: GrantEvidence | None
    probation: SkillProbation | None
    expert_application: ExpertApplicationEligibility
    next_grade: NextGrade | None


@dataclass(frozen=True, kw_only=True)
class ProfileWallet:
    settled: int
    reserved: int
    available: int
    transactions_url: str = "/api/v1/me/wallet/transactions"
    unit: str = "PRODиты"


@dataclass(frozen=True, kw_only=True)
class Profile:
    id: UUID
    display_name: str
    role: UserRole
    capabilities: tuple[ProductCapability, ...]
    grade: SkillGrade
    competencies: tuple[ProfileCompetency, ...]
    probation: tuple[SkillProbation, ...]
    wants_to_review: bool
    wallet: ProfileWallet
    karma: int
    prodits: int


@dataclass(frozen=True, kw_only=True)
class Activity:
    id: str
    type: str
    resource_id: UUID
    description: str
    created_at: datetime


class ProfileReader(Protocol):
    async def profile(self, actor: UserId) -> Profile: ...

    async def history(self, actor: UserId, *, limit: int, offset: int) -> tuple[Activity, ...]: ...


class ProfileQueries:
    def __init__(self, identity: IdentityProvider, reader: ProfileReader) -> None:
        self.identity = identity
        self.reader = reader

    async def profile(self) -> Profile:
        user = await self.identity.get_current_user()
        return await self.reader.profile(user.id)

    async def history(self, *, limit: int = 20, offset: int = 0) -> tuple[Activity, ...]:
        user = await self.identity.get_current_user()
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid history pagination")
        return await self.reader.history(user.id, limit=limit, offset=offset)

    async def development_policy(self) -> "DevelopmentPolicy":
        await self.identity.get_current_user()
        return DevelopmentPolicy()


@dataclass(frozen=True, kw_only=True)
class GradeGate:
    grade: SkillGrade
    minimum_value: int
    successful_cases: int
    confirmed_good_reviews: int
    previous_probation_required: bool
    independent_readiness_required: bool


@dataclass(frozen=True, kw_only=True)
class DevelopmentPolicy:
    # Actual persisted versions, not the draft API's unimplemented policy alias.
    version: str = POLICY_VERSION
    progression_version: str = PROGRESSION_POLICY_VERSION
    state: str = "configured"
    formula: str = "score * weight * (1 + 0.2 * E / 100)"
    success_score: int = 70
    quality_score: int = 70
    probation_required_reviews: int = 3
    display_cap: int = 100
    rounding: str = "ROUND_HALF_UP_2"
    priority_cost: int = PRIORITY_COST
    grade_gates: tuple[GradeGate, ...] = (
        GradeGate(
            grade=SkillGrade.PERFORMER,
            minimum_value=0,
            successful_cases=0,
            confirmed_good_reviews=0,
            previous_probation_required=False,
            independent_readiness_required=False,
        ),
        GradeGate(
            grade=SkillGrade.REVIEW_MINUS,
            minimum_value=25,
            successful_cases=2,
            confirmed_good_reviews=0,
            previous_probation_required=False,
            independent_readiness_required=False,
        ),
        GradeGate(
            grade=SkillGrade.REVIEW_PLUS,
            minimum_value=50,
            successful_cases=2,
            confirmed_good_reviews=3,
            previous_probation_required=True,
            independent_readiness_required=False,
        ),
        GradeGate(
            grade=SkillGrade.EXPERT,
            minimum_value=75,
            successful_cases=2,
            confirmed_good_reviews=3,
            previous_probation_required=True,
            independent_readiness_required=True,
        ),
    )
    matching: str = (
        "Every required skill needs a current confirmed or explicitly supervised non-expert grant, "
        "value >= required_value and grade >= max(case floor, next author snapshot grade capped at expert). "
        "Case floor: review_minus below 50, review_plus at 50..<75, confirmed expert at >=75. "
        "Prefer closest eligible grade; never relax rights. Quality requires independent confirmed experts "
        "in all case skills, excluding author and original reviewer. Overall profile grade grants no authority."
    )
    expertise_source: str = (
        "Validated reviewer per-skill snapshot, or confirming expert per-skill snapshot when supervised; "
        "karma never multiplies XP."
    )
