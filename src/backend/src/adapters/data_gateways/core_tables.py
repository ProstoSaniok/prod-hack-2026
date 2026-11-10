"""Git-neutral product metadata. Registration does not wire product use cases."""

from datetime import datetime
from enum import Enum as PythonEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import column_property, registry

from src.adapters.data_gateways.immutable_evidence import protect_evidence, protect_revision_comment
from src.application.catalog.entity import (
    Case,
    CaseCompetency,
    CaseStatus,
    Competency,
    ReferenceSolution,
    Repository,
    RepositoryPurpose,
    RetainedReference,
)
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.project.entity import Project, ProjectState, Submission, SubmissionCompetency
from src.application.review.entity import (
    AssessorSnapshot,
    AssignmentCompetency,
    AssignmentState,
    DiffSide,
    JobState,
    QualityAssessment,
    QualityDecision,
    ReviewAssignment,
    ReviewComment,
    ReviewDraft,
    ReviewJob,
    ReviewKind,
    ReviewResult,
    ReviewRevision,
    ReviewState,
    ReviewVerdict,
    ReviewerSnapshot,
)


def identity_columns() -> tuple[Column[UUID], Column[datetime], Column[datetime]]:
    return (
        Column("id", Uuid, primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("deleted_at", DateTime(timezone=True)),
    )


def enum_column(name: str, enum_type: type[PythonEnum], *, nullable: bool = False) -> Column[PythonEnum]:
    return Column(
        name,
        Enum(enum_type, name=f"core_{enum_type.__name__.lower()}_{name}", native_enum=False, create_constraint=True),
        nullable=nullable,
    )


def register_core_tables(metadata: MetaData, mappers: registry) -> None:
    competencies = Table(
        "competencies",
        metadata,
        *identity_columns(),
        Column("name", String(200), nullable=False, unique=True),
        Column("description", String, nullable=False),
        CheckConstraint("btrim(name) <> ''", name="ck_competency_name"),
    )
    repositories = Table(
        "repositories",
        metadata,
        *identity_columns(),
        Column("owner_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
        Column("provider", String(100), nullable=False),
        Column("external_id", String(500), nullable=False),
        enum_column("purpose", RepositoryPurpose),
        UniqueConstraint("id", "purpose", name="uq_repository_purpose"),
        UniqueConstraint("provider", "external_id", name="uq_repository_external_identity"),
        CheckConstraint("btrim(provider) <> '' AND btrim(external_id) <> ''", name="ck_repository_identity"),
    )
    retained_references = Table(
        "retained_references",
        metadata,
        *identity_columns(),
        Column("repository_id", Uuid, ForeignKey("repositories.id"), nullable=False),
        Column("external_id", String(500), nullable=False),
        Column("commit_id", String(500), nullable=False),
        UniqueConstraint("repository_id", "external_id", name="uq_retained_reference_identity"),
        UniqueConstraint("id", "repository_id", name="uq_retained_reference_repository"),
        CheckConstraint("btrim(external_id) <> '' AND btrim(commit_id) <> ''", name="ck_retained_reference_identity"),
    )
    cases = Table(
        "cases",
        metadata,
        *identity_columns(),
        Column("owner_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
        Column("title", String(500), nullable=False),
        Column("description", String, nullable=False),
        Column("expected_result", String, nullable=False),
        Column("materials_repository_id", Uuid),
        Column(
            "materials_repository_purpose",
            Enum(RepositoryPurpose, name="core_repositorypurpose", native_enum=False, create_constraint=True),
            nullable=False,
            server_default="MATERIALS",
        ),
        ForeignKeyConstraint(
            ["materials_repository_id", "materials_repository_purpose"],
            ["repositories.id", "repositories.purpose"],
            name="fk_case_materials_purpose",
        ),
        CheckConstraint("materials_repository_purpose = 'MATERIALS'", name="ck_case_materials_purpose"),
        enum_column("status", CaseStatus),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("frozen_at", DateTime(timezone=True)),
        CheckConstraint("version >= 1", name="ck_case_version"),
        CheckConstraint(
            "status <> 'PUBLISHED' OR (btrim(title) <> '' AND "
            "btrim(description) <> '' AND btrim(expected_result) <> '')",
            name="ck_case_complete_content",
            info={"prodguild_learning_check": True},
        ),
        Index("ix_cases_publication", "status", "created_at"),
    )
    case_competencies = Table(
        "case_competencies",
        metadata,
        *identity_columns(),
        Column("case_id", Uuid, ForeignKey("cases.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False, index=True),
        Column("required_value", Numeric, nullable=False),
        Column("weight", Numeric, nullable=False),
        UniqueConstraint("case_id", "competency_id", name="uq_case_competency"),
        CheckConstraint("required_value BETWEEN 0 AND 100", name="ck_case_required_value"),
        CheckConstraint("weight > 0 AND weight <= 0.25", name="ck_case_weight"),
    )
    reference_solutions = Table(
        "reference_solutions",
        metadata,
        *identity_columns(),
        Column("case_id", Uuid, ForeignKey("cases.id"), nullable=False, unique=True),
        Column("repository_id", Uuid, nullable=False, unique=True),
        Column(
            "repository_purpose",
            Enum(RepositoryPurpose, name="core_repositorypurpose", native_enum=False, create_constraint=True),
            nullable=False,
            server_default="REFERENCE",
        ),
        ForeignKeyConstraint(
            ["repository_id", "repository_purpose"],
            ["repositories.id", "repositories.purpose"],
            name="fk_reference_repository_purpose",
        ),
        CheckConstraint("repository_purpose = 'REFERENCE'", name="ck_reference_repository_purpose"),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("ready_reference_id", Uuid),
        Column("ready_at", DateTime(timezone=True)),
        ForeignKeyConstraint(
            ["ready_reference_id", "repository_id"],
            ["retained_references.id", "retained_references.repository_id"],
            name="fk_reference_ready_repository",
        ),
        CheckConstraint("version >= 1", name="ck_reference_version"),
        CheckConstraint("(ready_at IS NULL) = (ready_reference_id IS NULL)", name="ck_reference_readiness"),
    )
    projects = Table(
        "projects",
        metadata,
        *identity_columns(),
        Column("participant_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("case_id", Uuid, ForeignKey("cases.id"), nullable=False, index=True),
        Column("case_version", Integer, nullable=False),
        Column("repository_id", Uuid, ForeignKey("repositories.id"), nullable=False, unique=True),
        Column(
            "repository_purpose",
            Enum(RepositoryPurpose, name="core_repositorypurpose", native_enum=False, create_constraint=True),
            nullable=False,
            server_default="WORKSPACE",
        ),
        ForeignKeyConstraint(
            ["repository_id", "repository_purpose"],
            ["repositories.id", "repositories.purpose"],
            name="fk_project_repository_purpose",
        ),
        CheckConstraint("repository_purpose = 'WORKSPACE'", name="ck_project_repository_purpose"),
        Column("working_branch", String(500), nullable=False),
        enum_column("state", ProjectState),
        UniqueConstraint("participant_id", "case_id", name="uq_project_participant_case"),
        UniqueConstraint("id", "repository_id", "case_version", name="uq_project_submission_identity"),
        CheckConstraint("case_version >= 1", name="ck_project_case_version"),
        CheckConstraint("btrim(working_branch) <> ''", name="ck_project_working_branch"),
    )
    submissions = Table(
        "submissions",
        metadata,
        *identity_columns(),
        Column("project_id", Uuid, nullable=False, unique=True),
        Column("repository_id", Uuid, nullable=False),
        Column("retained_reference_id", Uuid, nullable=False),
        Column("case_version", Integer, nullable=False),
        Column("effective_review_id", Uuid),
        ForeignKeyConstraint(
            ["project_id", "repository_id", "case_version"],
            ["projects.id", "projects.repository_id", "projects.case_version"],
            name="fk_submission_project_snapshot",
        ),
        ForeignKeyConstraint(
            ["retained_reference_id", "repository_id"],
            ["retained_references.id", "retained_references.repository_id"],
            name="fk_submission_retained_repository",
        ),
        ForeignKeyConstraint(
            ["effective_review_id", "id"],
            ["review_results.id", "review_results.submission_id"],
            name="fk_submission_effective_review",
            use_alter=True,
        ),
        CheckConstraint("case_version >= 1", name="ck_submission_case_version"),
    )
    submission_competencies = Table(
        "submission_competencies",
        metadata,
        *identity_columns(),
        Column("submission_id", Uuid, ForeignKey("submissions.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("required_value", Numeric, nullable=False),
        Column("weight", Numeric, nullable=False),
        Column("author_value", Numeric, nullable=False),
        enum_column("author_grade", SkillGrade),
        UniqueConstraint("submission_id", "competency_id", name="uq_submission_competency"),
        CheckConstraint("required_value BETWEEN 0 AND 100", name="ck_submission_required_value"),
        CheckConstraint("weight > 0 AND weight <= 0.25", name="ck_submission_weight"),
        CheckConstraint("author_value BETWEEN 0 AND 100", name="ck_submission_author_value"),
    )
    review_jobs = Table(
        "review_jobs",
        metadata,
        *identity_columns(),
        Column("submission_id", Uuid, ForeignKey("submissions.id"), nullable=False, index=True),
        enum_column("kind", ReviewKind),
        Column("policy_version", String(100), nullable=False),
        Column("reviewed_revision_id", Uuid),
        enum_column("state", JobState),
        UniqueConstraint("id", "submission_id", "kind", name="uq_job_subject_kind"),
        UniqueConstraint("id", "reviewed_revision_id", name="uq_job_reviewed_revision"),
        ForeignKeyConstraint(
            ["reviewed_revision_id", "submission_id"],
            ["review_revisions.id", "review_revisions.submission_id"],
            name="fk_quality_job_revision_subject",
            use_alter=True,
        ),
        CheckConstraint("(kind = 'QUALITY_CHECK') = (reviewed_revision_id IS NOT NULL)", name="ck_job_quality_subject"),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_job_policy_version"),
        Index("uq_primary_job_submission", "submission_id", unique=True, postgresql_where=text("kind = 'REVIEW'")),
        Index(
            "uq_quality_job_revision",
            "reviewed_revision_id",
            unique=True,
            postgresql_where=text("kind = 'QUALITY_CHECK'"),
        ),
        Index("ix_review_jobs_queue", "state", "kind", "created_at"),
    )
    review_assignments = Table(
        "review_assignments",
        metadata,
        *identity_columns(),
        Column("job_id", Uuid, nullable=False),
        Column("submission_id", Uuid, nullable=False),
        enum_column("kind", ReviewKind),
        Column("reviewer_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("lease_expires_at", DateTime(timezone=True)),
        Column("supervised", Boolean, nullable=False),
        enum_column("state", AssignmentState),
        ForeignKeyConstraint(
            ["job_id", "submission_id", "kind"],
            ["review_jobs.id", "review_jobs.submission_id", "review_jobs.kind"],
            name="fk_assignment_job_subject",
        ),
        UniqueConstraint("id", "submission_id", "reviewer_id", "kind", name="uq_assignment_review_identity"),
        UniqueConstraint("id", "job_id", name="uq_assignment_job"),
        UniqueConstraint("id", "job_id", "submission_id", "reviewer_id", "kind", name="uq_assignment_quality_identity"),
        CheckConstraint("lease_expires_at > created_at", name="ck_assignment_lease"),
        Index("uq_assignment_active_job", "job_id", unique=True, postgresql_where=text("state = 'ACTIVE'")),
        Index("uq_assignment_active_reviewer", "reviewer_id", unique=True, postgresql_where=text("state = 'ACTIVE'")),
    )
    assignment_competencies = Table(
        "assignment_competencies",
        metadata,
        *identity_columns(),
        Column("assignment_id", Uuid, ForeignKey("review_assignments.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("grant_id", Uuid, nullable=False),
        Column("grant_version", Integer, nullable=False),
        Column(
            "user_competency_version",
            Integer,
            CheckConstraint("user_competency_version >= 1", name="ck_assignment_skill_competency_version"),
            nullable=False,
            info={"prodguild_snapshot_version": True},
        ),
        Column("value", Numeric, nullable=False),
        enum_column("grade", SkillGrade),
        enum_column("grant_state", SkillGrantState),
        Column("probation_id", Uuid, ForeignKey("probations.id")),
        ForeignKeyConstraint(
            ["grant_id", "competency_id"],
            ["skill_grants.id", "skill_grants.competency_id"],
            name="fk_assignment_snapshot_grant_skill",
        ),
        UniqueConstraint("assignment_id", "competency_id", name="uq_assignment_competency"),
        CheckConstraint("value BETWEEN 0 AND 100 AND grant_version >= 1", name="ck_assignment_qualification_snapshot"),
    )
    review_drafts = Table(
        "review_drafts",
        metadata,
        *identity_columns(),
        Column("assignment_id", Uuid, ForeignKey("review_assignments.id"), nullable=False, unique=True),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("score", Numeric),
        enum_column("verdict", ReviewVerdict, nullable=True),
        Column("strengths", String, nullable=False),
        Column("problems", String, nullable=False),
        Column("next_steps", String, nullable=False),
        Column("updated_at", DateTime(timezone=True)),
        CheckConstraint("version >= 1", name="ck_draft_version"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_draft_score"),
    )
    review_results = Table(
        "review_results",
        metadata,
        *identity_columns(),
        Column("assignment_id", Uuid, nullable=False, unique=True),
        Column("submission_id", Uuid, ForeignKey("submissions.id"), nullable=False),
        Column("reviewer_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
        enum_column("kind", ReviewKind),
        enum_column("state", ReviewState),
        Column("published_at", DateTime(timezone=True)),
        Column("published_revision_id", Uuid),
        ForeignKeyConstraint(
            ["assignment_id", "submission_id", "reviewer_id", "kind"],
            [
                "review_assignments.id",
                "review_assignments.submission_id",
                "review_assignments.reviewer_id",
                "review_assignments.kind",
            ],
            name="fk_result_assignment_identity",
        ),
        ForeignKeyConstraint(
            ["published_revision_id", "id"],
            ["review_revisions.id", "review_revisions.review_id"],
            name="fk_result_published_revision",
            use_alter=True,
        ),
        UniqueConstraint("id", "submission_id", name="uq_review_result_subject"),
        UniqueConstraint("id", "reviewer_id", name="uq_review_result_reviewer"),
        CheckConstraint("kind IN ('REVIEW', 'RECHECK')", name="ck_solution_review_kind"),
        CheckConstraint(
            "(state = 'PUBLISHED' AND published_at IS NOT NULL AND published_revision_id IS NOT NULL) OR "
            "(state <> 'PUBLISHED' AND published_at IS NULL AND published_revision_id IS NULL)",
            name="ck_review_publication",
        ),
    )
    review_revisions = Table(
        "review_revisions",
        metadata,
        *identity_columns(),
        Column("review_id", Uuid, nullable=False),
        Column("submission_id", Uuid, nullable=False),
        Column("number", Integer, nullable=False),
        Column("source_draft_version", Integer),
        Column("policy_version", String(100), nullable=False),
        Column("score", Numeric, nullable=False),
        enum_column("verdict", ReviewVerdict),
        Column("strengths", String, nullable=False),
        Column("problems", String, nullable=False),
        Column("next_steps", String, nullable=False),
        ForeignKeyConstraint(
            ["review_id", "submission_id"],
            ["review_results.id", "review_results.submission_id"],
            name="fk_revision_review_subject",
        ),
        UniqueConstraint("review_id", "number", name="uq_review_revision_number"),
        UniqueConstraint("id", "submission_id", name="uq_review_revision_subject"),
        UniqueConstraint("id", "review_id", name="uq_review_revision_identity"),
        CheckConstraint("number >= 1 AND score BETWEEN 0 AND 100", name="ck_revision_score_number"),
        CheckConstraint("source_draft_version >= 1", name="ck_revision_source_draft_version"),
        CheckConstraint(
            "btrim(strengths) <> '' AND btrim(problems) <> '' AND btrim(next_steps) <> '' "
            "AND btrim(policy_version) <> ''",
            name="ck_revision_complete_feedback",
        ),
    )
    review_comments = Table(
        "review_comments",
        metadata,
        *identity_columns(),
        Column("text", String, nullable=False),
        Column("draft_id", Uuid, ForeignKey("review_drafts.id")),
        Column("revision_id", Uuid, ForeignKey("review_revisions.id")),
        Column("path", String),
        Column("line", Integer),
        Column("end_line", Integer),
        enum_column("side", DiffSide, nullable=True),
        CheckConstraint("(draft_id IS NULL) <> (revision_id IS NULL)", name="ck_comment_owner"),
        CheckConstraint("btrim(text) <> ''", name="ck_comment_text"),
        CheckConstraint(
            "end_line IS NULL OR (line IS NOT NULL AND end_line >= line)",
            name="ck_comment_range",
            info={"prodguild_learning_check": True},
        ),
        CheckConstraint(
            "(line IS NULL AND side IS NULL) OR "
            "(path IS NOT NULL AND line IS NOT NULL AND line > 0 AND side IS NOT NULL)",
            name="ck_comment_position",
        ),
    )
    reviewer_snapshots = Table(
        "reviewer_snapshots",
        metadata,
        *identity_columns(),
        Column("revision_id", Uuid, ForeignKey("review_revisions.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("value", Numeric, nullable=False),
        enum_column("grade", SkillGrade),
        Column("grant_id", Uuid, nullable=False),
        Column("grant_version", Integer, nullable=False),
        Column(
            "user_competency_version",
            Integer,
            CheckConstraint("user_competency_version >= 1", name="ck_reviewer_snapshot_competency_version"),
            nullable=False,
            info={"prodguild_snapshot_version": True},
        ),
        enum_column("grant_state", SkillGrantState),
        ForeignKeyConstraint(
            ["grant_id", "competency_id"],
            ["skill_grants.id", "skill_grants.competency_id"],
            name="fk_reviewer_snapshot_grant_skill",
        ),
        UniqueConstraint("revision_id", "competency_id", name="uq_reviewer_snapshot_skill"),
        CheckConstraint("value BETWEEN 0 AND 100 AND grant_version >= 1", name="ck_reviewer_snapshot_value"),
    )
    quality_assessments = Table(
        "quality_assessments",
        metadata,
        *identity_columns(),
        Column("assignment_id", Uuid, nullable=False, unique=True),
        Column("job_id", Uuid, nullable=False),
        Column("submission_id", Uuid, nullable=False),
        Column("revision_id", Uuid, nullable=False),
        Column("assessor_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("quality_score", Numeric, nullable=False),
        enum_column("decision", QualityDecision),
        Column("feedback", String, nullable=False),
        enum_column("kind", ReviewKind),
        UniqueConstraint(
            "id", "revision_id", "assessor_id", "quality_score", "decision", name="uq_quality_confirmation_evidence"
        ),
        ForeignKeyConstraint(
            ["assignment_id", "job_id", "submission_id", "assessor_id", "kind"],
            [
                "review_assignments.id",
                "review_assignments.job_id",
                "review_assignments.submission_id",
                "review_assignments.reviewer_id",
                "review_assignments.kind",
            ],
            name="fk_quality_assignment_identity",
        ),
        ForeignKeyConstraint(
            ["job_id", "revision_id"],
            ["review_jobs.id", "review_jobs.reviewed_revision_id"],
            name="fk_quality_assigned_revision",
        ),
        ForeignKeyConstraint(
            ["revision_id", "submission_id"],
            ["review_revisions.id", "review_revisions.submission_id"],
            name="fk_quality_revision_subject",
        ),
        CheckConstraint("kind = 'QUALITY_CHECK'", name="ck_quality_kind"),
        CheckConstraint("quality_score BETWEEN 0 AND 100", name="ck_quality_score"),
        CheckConstraint("decision <> 'CONFIRMED' OR quality_score >= 70", name="ck_quality_confirmation"),
        CheckConstraint("btrim(feedback) <> ''", name="ck_quality_feedback"),
    )
    assessor_snapshots = Table(
        "assessor_snapshots",
        metadata,
        *identity_columns(),
        Column("assessment_id", Uuid, ForeignKey("quality_assessments.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("value", Numeric, nullable=False),
        Column("grant_id", Uuid, nullable=False),
        Column("grant_version", Integer, nullable=False),
        Column(
            "user_competency_version",
            Integer,
            CheckConstraint("user_competency_version >= 1", name="ck_assessor_snapshot_competency_version"),
            nullable=False,
            info={"prodguild_snapshot_version": True},
        ),
        enum_column("grade", SkillGrade),
        enum_column("grant_state", SkillGrantState),
        ForeignKeyConstraint(
            ["grant_id", "competency_id", "grade"],
            ["skill_grants.id", "skill_grants.competency_id", "skill_grants.grade"],
            name="fk_assessor_snapshot_expert_skill",
        ),
        UniqueConstraint("assessment_id", "competency_id", name="uq_assessor_snapshot_skill"),
        UniqueConstraint(
            "assessment_id", "competency_id", "grant_id", "grant_version", name="uq_assessor_grant_evidence"
        ),
        CheckConstraint("value BETWEEN 75 AND 100 AND grant_version >= 1", name="ck_assessor_snapshot_value"),
        CheckConstraint("grade = 'EXPERT' AND grant_state = 'CONFIRMED'", name="ck_assessor_snapshot_expert"),
    )
    for entity, table in (
        (Competency, competencies),
        (Repository, repositories),
        (RetainedReference, retained_references),
        (Case, cases),
        (CaseCompetency, case_competencies),
        (ReferenceSolution, reference_solutions),
        (Project, projects),
        (Submission, submissions),
        (SubmissionCompetency, submission_competencies),
        (ReviewJob, review_jobs),
        (ReviewAssignment, review_assignments),
        (AssignmentCompetency, assignment_competencies),
        (ReviewDraft, review_drafts),
        (ReviewResult, review_results),
        (ReviewRevision, review_revisions),
        (ReviewComment, review_comments),
        (ReviewerSnapshot, reviewer_snapshots),
        (QualityAssessment, quality_assessments),
        (AssessorSnapshot, assessor_snapshots),
    ):
        version_column = table.c.get("version")
        if entity is ReviewComment:
            mappers.map_imperatively(
                entity, table, properties={"revision_id": column_property(table.c.revision_id, active_history=True)}
            )
        elif version_column is None:
            mappers.map_imperatively(entity, table)
        else:
            mappers.map_imperatively(entity, table, version_id_col=version_column)
    for evidence in (
        RetainedReference,
        SubmissionCompetency,
        AssignmentCompetency,
        ReviewRevision,
        ReviewerSnapshot,
        QualityAssessment,
        AssessorSnapshot,
    ):
        protect_evidence(evidence)
    event.listen(ReviewComment, "before_update", protect_revision_comment)
    event.listen(ReviewComment, "before_delete", protect_revision_comment)
