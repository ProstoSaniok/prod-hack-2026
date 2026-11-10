from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.common.entity import Entity, to_entity
from src.application.user.entity import UserId

CompetencyId = NewType("CompetencyId", UUID)
CaseId = NewType("CaseId", UUID)
CaseCompetencyId = NewType("CaseCompetencyId", UUID)
CaseContentVersionId = NewType("CaseContentVersionId", UUID)
CaseContentCompetencyId = NewType("CaseContentCompetencyId", UUID)
RepositoryId = NewType("RepositoryId", UUID)
RetainedReferenceId = NewType("RetainedReferenceId", UUID)
ReferenceSolutionId = NewType("ReferenceSolutionId", UUID)


class CaseStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RepositoryPurpose(StrEnum):
    MATERIALS = "materials"
    PUBLISHED_MATERIALS = "published_materials"
    REFERENCE = "reference"
    WORKSPACE = "workspace"
    REVIEW = "review"


@to_entity
class Competency(Entity[CompetencyId]):
    name: str
    description: str


@to_entity
class Repository(Entity[RepositoryId]):
    owner_id: UserId
    provider: str
    external_id: str
    purpose: RepositoryPurpose


@to_entity
class RetainedReference(Entity[RetainedReferenceId]):
    """Identity of an externally retained object, not proof that an ACL exists."""

    repository_id: RepositoryId
    external_id: str
    commit_id: str


@to_entity
class Case(Entity[CaseId]):
    owner_id: UserId
    title: str
    description: str
    expected_result: str
    materials_repository_id: RepositoryId | None = None
    materials_repository_purpose: RepositoryPurpose = RepositoryPurpose.MATERIALS
    status: CaseStatus = CaseStatus.DRAFT
    version: int = 1
    frozen_at: datetime | None = None
    current_published_content_id: CaseContentVersionId | None = None
    updated_at: datetime | None = None


@to_entity
class CaseCompetency(Entity[CaseCompetencyId]):
    case_id: CaseId
    competency_id: CompetencyId
    required_value: Decimal
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.required_value.is_finite() or not 0 <= self.required_value <= 100:
            raise ValueError("Required skill value must be between 0 and 100")
        if not self.weight.is_finite() or not 0 < self.weight <= Decimal("0.25"):
            raise ValueError("Progression weight must be positive and at most 0.25")


@to_entity
class ReferenceSolution(Entity[ReferenceSolutionId]):
    case_id: CaseId
    repository_id: RepositoryId
    repository_purpose: RepositoryPurpose = RepositoryPurpose.REFERENCE
    version: int = 1
    ready_reference_id: RetainedReferenceId | None = None
    ready_at: datetime | None = None
    updated_at: datetime | None = None


@to_entity
class CaseContentVersion(Entity[CaseContentVersionId]):
    """Write-once publication; source/reference provenance is strictly private."""

    case_id: CaseId
    content_version: int
    title: str
    description: str
    expected_result: str
    published_repository_id: RepositoryId
    published_retained_reference_id: RetainedReferenceId
    starter_commit_sha: str
    starter_tree_sha: str
    source_repository_id: RepositoryId
    source_retained_reference_id: RetainedReferenceId
    reference_solution_id: ReferenceSolutionId
    reference_version: int
    reference_repository_id: RepositoryId
    ready_reference_id: RetainedReferenceId
    published_repository_purpose: RepositoryPurpose = RepositoryPurpose.PUBLISHED_MATERIALS
    source_repository_purpose: RepositoryPurpose = RepositoryPurpose.MATERIALS


@to_entity
class CaseContentCompetency(Entity[CaseContentCompetencyId]):
    content_version_id: CaseContentVersionId
    competency_id: CompetencyId
    required_value: Decimal
    weight: Decimal
