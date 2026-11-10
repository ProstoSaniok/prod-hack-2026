from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from src.application.catalog.entity import Case, CaseId, CaseStatus, CompetencyId
from src.application.common.errors import ConflictError, NotFoundError, ValidationError
from src.application.common.identity_provider import IdentityProvider
from src.application.common.interactor import to_interactor
from src.application.user.entity import UserId


class CaseNotFoundError(NotFoundError):
    pass


class CaseValidationError(ValidationError):
    pass


class CaseConflictError(ConflictError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CaseDifficulty(StrEnum):
    REVIEW_MINUS = "review_minus"
    REVIEW_PLUS = "review_plus"
    EXPERT = "expert"


@dataclass(frozen=True, kw_only=True)
class CaseSkill:
    competency_id: CompetencyId
    required_value: Decimal
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.required_value.is_finite() or not 0 <= self.required_value <= 100:
            raise CaseValidationError("Required skill value must be between 0 and 100")
        if not self.weight.is_finite() or not 0 < self.weight <= Decimal("0.25"):
            raise CaseValidationError("Weight must be positive and at most 0.25")


@dataclass(frozen=True, kw_only=True)
class CaseMetadata:
    title: str
    description: str
    expected_result: str
    competencies: tuple[CaseSkill, ...]

    def __post_init__(self) -> None:
        if len(self.title) > 500:
            raise CaseValidationError("Title must contain at most 500 characters")
        if any(len(value.encode()) > 16_384 for value in (self.description, self.expected_result)):
            raise CaseValidationError("Description and expected result must contain at most 16384 UTF-8 bytes")
        if len(self.competencies) > 100:
            raise CaseValidationError("A case supports at most 100 competencies")
        if len({skill.competency_id for skill in self.competencies}) != len(self.competencies):
            raise CaseValidationError("Duplicate competency")

    @property
    def difficulty(self) -> CaseDifficulty:
        maximum = max((skill.required_value for skill in self.competencies), default=Decimal(0))
        if maximum >= 75:
            return CaseDifficulty.EXPERT
        if maximum >= 50:
            return CaseDifficulty.REVIEW_PLUS
        return CaseDifficulty.REVIEW_MINUS

    def require_publishable(self) -> None:
        if not all(value.strip() for value in (self.title, self.description, self.expected_result)):
            raise CaseConflictError("CASE_NOT_READY", "Fill in the title, description and expected result")
        if not self.competencies:
            raise CaseConflictError("CASE_NOT_READY", "Select at least one competency")


@dataclass(frozen=True, kw_only=True)
class ManagedCase:
    case: Case
    metadata: CaseMetadata


@dataclass(frozen=True, kw_only=True)
class CaseChanges:
    expected_version: int
    title: str | None = None
    description: str | None = None
    expected_result: str | None = None
    competencies: tuple[CaseSkill, ...] | None = None
    difficulty: CaseDifficulty | None = None
    status: CaseStatus | None = None

    @property
    def changes_content(self) -> bool:
        return any(
            value is not None for value in (self.title, self.description, self.expected_result, self.competencies)
        )


class CaseGateway(Protocol):
    async def require_organizer(self, actor_id: UserId, *, lock: bool = False) -> None: ...

    async def get_owned(self, actor_id: UserId, case_id: CaseId, *, lock: bool = False) -> ManagedCase | None: ...

    async def list_owned(
        self, actor_id: UserId, *, limit: int, offset: int, status: CaseStatus | None
    ) -> list[ManagedCase]: ...

    async def validate_skills(self, skills: tuple[CaseSkill, ...]) -> None: ...

    async def has_projects(self, case_id: CaseId) -> bool: ...

    async def save(self, case: Case, skills: tuple[CaseSkill, ...] | None, *, creating: bool = False) -> None: ...

    async def create_if_absent(self, case: Case, skills: tuple[CaseSkill, ...]) -> bool: ...


class CaseAccess:
    def __init__(self, identity: IdentityProvider, gateway: CaseGateway) -> None:
        self.identity = identity
        self.gateway = gateway

    async def authorize(self, case_id: CaseId | None = None, *, include_deleted: bool = False) -> UserId:
        actor = await self.identity.get_current_user()
        # Resource ownership is checked before capability errors to hide foreign IDs.
        if case_id is not None:
            item = await self.gateway.get_owned(actor.id, case_id)
            if item is None or (item.case.deleted_at is not None and not include_deleted):
                raise CaseNotFoundError
        await self.gateway.require_organizer(actor.id)
        return actor.id

    async def lock(self, case_id: CaseId, expected_version: int) -> ManagedCase:
        actor = await self.authorize(case_id)
        await self.gateway.require_organizer(actor, lock=True)
        item = await self.gateway.get_owned(actor, case_id, lock=True)
        if item is None or item.case.deleted_at is not None:
            raise CaseNotFoundError
        if item.case.version != expected_version:
            raise CaseConflictError("STALE_VERSION", "Case was changed; reload before saving")
        return item

    async def require_editable(self, case: Case) -> None:
        if case.frozen_at is not None or await self.gateway.has_projects(case.id):
            raise CaseConflictError("CASE_FROZEN", "Content is frozen after the first project")

    @staticmethod
    def check_difficulty(metadata: CaseMetadata, supplied: CaseDifficulty | None) -> None:
        if supplied is not None and supplied != metadata.difficulty:
            raise CaseValidationError("Difficulty must match competency requirements")


@to_interactor
class CaseQueries:
    access: CaseAccess
    gateway: CaseGateway

    async def list_cases(self, *, limit: int, offset: int, status: CaseStatus | None) -> list[ManagedCase]:
        actor = await self.access.authorize()
        if not 1 <= limit <= 100 or offset < 0:
            raise CaseValidationError("Invalid pagination")
        if offset > 9_223_372_036_854_775_807:
            return []
        return await self.gateway.list_owned(actor, limit=limit, offset=offset, status=status)

    async def get_case(self, case_id: CaseId) -> ManagedCase:
        actor = await self.access.authorize(case_id)
        item = await self.gateway.get_owned(actor, case_id)
        if item is None or item.case.deleted_at is not None:
            raise CaseNotFoundError
        return item
