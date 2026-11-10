from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.catalog.entity import (
    CaseContentVersionId,
    CaseId,
    CompetencyId,
    RepositoryId,
    RepositoryPurpose,
    RetainedReferenceId,
)
from src.application.common.entity import Entity, to_entity
from src.application.development.entity import (
    ReviewerApplicationAssessmentId,
    SkillGrade,
    SkillGrantEvidenceKind,
    SkillGrantId,
    SkillGrantState,
)
from src.application.user.entity import UserId

ProjectId = NewType("ProjectId", UUID)
SubmissionId = NewType("SubmissionId", UUID)
SubmissionCompetencyId = NewType("SubmissionCompetencyId", UUID)


class ProjectState(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"


@to_entity
class Project(Entity[ProjectId]):
    participant_id: UserId
    case_id: CaseId
    case_version: int
    repository_id: RepositoryId
    working_branch: str
    state: ProjectState = ProjectState.ACTIVE
    repository_purpose: RepositoryPurpose = RepositoryPurpose.WORKSPACE
    # Nullable only for pre-publication-schema rows; new flows require all facts.
    content_version_id: CaseContentVersionId | None = None
    start_operation_id: UUID | None = None
    base_retained_reference_id: RetainedReferenceId | None = None
    workspace_base_commit_sha: str | None = None
    workspace_base_tree_sha: str | None = None
    updated_at: datetime | None = None


@to_entity
class Submission(Entity[SubmissionId]):
    project_id: ProjectId
    repository_id: RepositoryId
    retained_reference_id: RetainedReferenceId
    case_version: int
    effective_review_id: UUID | None = None
    operation_id: UUID | None = None
    content_version_id: CaseContentVersionId | None = None
    author_id: UserId | None = None
    base_retained_reference_id: RetainedReferenceId | None = None


@to_entity
class SubmissionCompetency(Entity[SubmissionCompetencyId]):
    submission_id: SubmissionId
    competency_id: CompetencyId
    required_value: Decimal
    weight: Decimal
    author_value: Decimal
    author_grade: SkillGrade
    content_version_id: CaseContentVersionId | None = None
    author_id: UserId | None = None
    author_user_competency_version: int | None = None
    author_snapshotted_at: datetime | None = None
    author_grant_id: SkillGrantId | None = None
    author_grant_version: int | None = None
    author_grant_state: SkillGrantState | None = None
    author_grant_grade: SkillGrade | None = None
    author_grant_user_competency_version: int | None = None
    author_grant_evidence_kind: SkillGrantEvidenceKind | None = None
    author_grant_evidence: str | None = None
    author_readiness_assessment_id: ReviewerApplicationAssessmentId | None = None
    author_confirmed_by_user_id: UserId | None = None
    author_confirmed_by_grant_id: SkillGrantId | None = None
    author_confirmed_by_grant_version: int | None = None
    author_seed_reference: str | None = None
    author_grant_confirmed_at: datetime | None = None
    author_grant_revoked_at: datetime | None = None
