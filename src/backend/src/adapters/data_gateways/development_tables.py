from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
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

from src.adapters.data_gateways.core_tables import enum_column, identity_columns
from src.adapters.data_gateways.immutable_evidence import (
    protect_evidence,
    protect_evidence_columns,
    protect_grant_transition,
)
from src.application.development.entity import (
    Probation,
    ProbationSuccess,
    ProgressionContribution,
    ReadinessDecision,
    ReviewerApplication,
    ReviewerApplicationAssessment,
    ReviewerApplicationState,
    SkillGrade,
    SkillGrant,
    SkillGrantEvidenceKind,
    SkillGrantState,
    UserCompetency,
)
from src.application.user.capability import ProductCapability, UserCapability, UserProfile


def register_development_tables(metadata: MetaData, mappers: registry) -> None:
    user_profiles = Table(
        "user_profiles",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, unique=True),
        Column("display_name", String(200), nullable=False),
        Column("wants_to_review", Boolean, nullable=False, server_default="false"),
        Column("version", Integer, nullable=False, server_default="1"),
        CheckConstraint("btrim(display_name) <> '' AND version >= 1", name="ck_profile_content_version"),
    )
    user_capabilities = Table(
        "user_capabilities",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
        enum_column("capability", ProductCapability),
        Column("revoked_at", DateTime(timezone=True)),
        Column("version", Integer, nullable=False, server_default="1"),
        CheckConstraint("version >= 1", name="ck_capability_version"),
        Index(
            "uq_live_product_capability",
            "user_id",
            "capability",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND deleted_at IS NULL"),
        ),
    )
    user_competencies = Table(
        "user_competencies",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False, index=True),
        Column("contribution_sum", Numeric, nullable=False),
        Column("displayed_value", Numeric, nullable=False),
        enum_column("grade", SkillGrade),
        Column("version", Integer, nullable=False, server_default="1"),
        UniqueConstraint("user_id", "competency_id", name="uq_user_competency"),
        CheckConstraint(
            "contribution_sum >= 0 AND contribution_sum < 'Infinity'::numeric", name="ck_skill_uncapped_sum"
        ),
        CheckConstraint("displayed_value BETWEEN 0 AND 100 AND version >= 1", name="ck_skill_value_version"),
    )
    skill_grants = Table(
        "skill_grants",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, nullable=False),
        Column("competency_id", Uuid, nullable=False),
        Column("user_competency_version", Integer, nullable=False),
        enum_column("grade", SkillGrade),
        enum_column("state", SkillGrantState),
        enum_column("evidence_kind", SkillGrantEvidenceKind),
        Column("evidence", String, nullable=False),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("readiness_assessment_id", Uuid),
        Column("confirmed_by_user_id", Uuid, ForeignKey("users.id")),
        Column("confirmed_by_grant_id", Uuid),
        Column("confirmed_by_grant_version", Integer),
        Column("confirmed_at", DateTime(timezone=True)),
        Column("seed_reference", String),
        Column("revoked_at", DateTime(timezone=True)),
        enum_column("readiness_decision", ReadinessDecision),
        enum_column("confirmed_by_grade", SkillGrade),
        ForeignKeyConstraint(
            ["user_id", "competency_id"],
            ["user_competencies.user_id", "user_competencies.competency_id"],
            name="fk_grant_user_competency",
        ),
        UniqueConstraint("id", "user_id", "competency_id", "grade", name="uq_grant_skill_grade"),
        UniqueConstraint("id", "user_id", "competency_id", name="uq_grant_user_skill"),
        UniqueConstraint("id", "competency_id", name="uq_grant_skill"),
        UniqueConstraint("id", "competency_id", "grade", name="uq_grant_skill_snapshot_grade"),
        ForeignKeyConstraint(
            ["confirmed_by_grant_id", "confirmed_by_user_id", "competency_id", "confirmed_by_grade"],
            ["skill_grants.id", "skill_grants.user_id", "skill_grants.competency_id", "skill_grants.grade"],
            name="fk_grant_confirming_expert_skill",
        ),
        ForeignKeyConstraint(
            [
                "readiness_assessment_id",
                "user_id",
                "competency_id",
                "grade",
                "confirmed_by_user_id",
                "confirmed_by_grant_id",
                "confirmed_by_grant_version",
                "readiness_decision",
            ],
            [
                "reviewer_application_assessments.id",
                "reviewer_application_assessments.applicant_id",
                "reviewer_application_assessments.competency_id",
                "reviewer_application_assessments.requested_grade",
                "reviewer_application_assessments.expert_user_id",
                "reviewer_application_assessments.expert_grant_id",
                "reviewer_application_assessments.expert_grant_version",
                "reviewer_application_assessments.decision",
            ],
            name="fk_grant_confirmed_readiness",
            use_alter=True,
        ),
        CheckConstraint("version >= 1 AND user_competency_version >= 1", name="ck_grant_versions"),
        CheckConstraint(
            "confirmed_by_grant_version IS NULL OR confirmed_by_grant_version >= 1", name="ck_grant_expert_version"
        ),
        CheckConstraint("grade <> 'PERFORMER' AND btrim(evidence) <> ''", name="ck_grant_evidence"),
        CheckConstraint("(state = 'REVOKED') = (revoked_at IS NOT NULL)", name="ck_grant_revocation"),
        CheckConstraint("state <> 'CONFIRMED' OR confirmed_at IS NOT NULL", name="ck_grant_confirmation"),
        CheckConstraint("confirmed_by_user_id <> user_id AND confirmed_by_grant_id <> id", name="ck_grant_independent"),
        CheckConstraint(
            "evidence_kind <> 'SEED' OR (seed_reference IS NOT NULL AND btrim(seed_reference) <> '' "
            "AND readiness_assessment_id IS NULL)",
            name="ck_grant_explicit_seed",
        ),
        CheckConstraint(
            "grade <> 'EXPERT' OR confirmed_at IS NULL OR evidence_kind = 'SEED' OR "
            "(evidence_kind = 'INDEPENDENT_READINESS' AND readiness_assessment_id IS NOT NULL "
            "AND confirmed_by_user_id IS NOT NULL AND confirmed_by_grant_id IS NOT NULL "
            "AND confirmed_by_grant_version IS NOT NULL)",
            name="ck_grant_expert_evidence",
        ),
        CheckConstraint(
            "readiness_decision = 'CONFIRMED' AND confirmed_by_grade = 'EXPERT'",
            name="ck_grant_readiness_discriminator",
        ),
        Index(
            "uq_live_skill_grant",
            "user_id",
            "competency_id",
            unique=True,
            postgresql_where=text("state <> 'REVOKED' AND deleted_at IS NULL"),
        ),
    )
    probations = Table(
        "probations",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        enum_column("grade", SkillGrade),
        Column("skill_grant_id", Uuid, nullable=False, unique=True),
        Column("epoch", Integer, nullable=False),
        Column("required_successes", Integer, nullable=False, server_default="3"),
        Column("completed_at", DateTime(timezone=True)),
        Column("version", Integer, nullable=False, server_default="1"),
        ForeignKeyConstraint(
            ["skill_grant_id", "user_id", "competency_id", "grade"],
            ["skill_grants.id", "skill_grants.user_id", "skill_grants.competency_id", "skill_grants.grade"],
            name="fk_probation_grant_identity",
        ),
        UniqueConstraint("user_id", "competency_id", "grade", "epoch", name="uq_probation_epoch"),
        UniqueConstraint("id", "user_id", "competency_id", name="uq_probation_reviewer_skill"),
        CheckConstraint("grade IN ('REVIEW_MINUS', 'REVIEW_PLUS')", name="ck_probation_grade"),
        CheckConstraint("epoch >= 1 AND version >= 1 AND required_successes = 3", name="ck_probation_requirements"),
        Index(
            "uq_active_probation_skill",
            "user_id",
            "competency_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL AND deleted_at IS NULL"),
        ),
    )
    probation_successes = Table(
        "probation_successes",
        metadata,
        *identity_columns(),
        Column("probation_id", Uuid, nullable=False),
        Column("reviewer_id", Uuid, nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("review_id", Uuid, nullable=False),
        Column("review_revision_id", Uuid, nullable=False),
        Column("quality_assessment_id", Uuid, nullable=False),
        Column("expert_user_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("expert_grant_id", Uuid, nullable=False),
        Column("expert_grant_version", Integer, nullable=False),
        Column("quality_score", Numeric, nullable=False),
        enum_column("quality_decision", ReadinessDecision),
        ForeignKeyConstraint(
            ["probation_id", "reviewer_id", "competency_id"],
            ["probations.id", "probations.user_id", "probations.competency_id"],
            name="fk_success_probation_identity",
        ),
        ForeignKeyConstraint(
            ["review_id", "reviewer_id"],
            ["review_results.id", "review_results.reviewer_id"],
            name="fk_success_underlying_reviewer",
        ),
        ForeignKeyConstraint(
            ["review_revision_id", "review_id"],
            ["review_revisions.id", "review_revisions.review_id"],
            name="fk_success_review_revision",
        ),
        ForeignKeyConstraint(
            ["quality_assessment_id", "review_revision_id", "expert_user_id", "quality_score", "quality_decision"],
            [
                "quality_assessments.id",
                "quality_assessments.revision_id",
                "quality_assessments.assessor_id",
                "quality_assessments.quality_score",
                "quality_assessments.decision",
            ],
            name="fk_success_confirmed_quality",
        ),
        ForeignKeyConstraint(
            ["expert_grant_id", "expert_user_id", "competency_id"],
            ["skill_grants.id", "skill_grants.user_id", "skill_grants.competency_id"],
            name="fk_success_expert_skill",
        ),
        ForeignKeyConstraint(
            ["quality_assessment_id", "competency_id", "expert_grant_id", "expert_grant_version"],
            [
                "assessor_snapshots.assessment_id",
                "assessor_snapshots.competency_id",
                "assessor_snapshots.grant_id",
                "assessor_snapshots.grant_version",
            ],
            name="fk_success_assessor_snapshot",
        ),
        UniqueConstraint("probation_id", "review_id", name="uq_probation_underlying_review"),
        CheckConstraint("reviewer_id <> expert_user_id", name="ck_success_independent"),
        CheckConstraint("quality_score BETWEEN 70 AND 100 AND expert_grant_version >= 1", name="ck_success_quality"),
        CheckConstraint("quality_decision = 'CONFIRMED'", name="ck_success_confirmed"),
    )
    reviewer_applications = Table(
        "reviewer_applications",
        metadata,
        *identity_columns(),
        Column("user_id", Uuid, nullable=False),
        Column("competency_id", Uuid, nullable=False),
        enum_column("requested_grade", SkillGrade),
        Column("user_competency_version", Integer, nullable=False),
        Column("evidence", String, nullable=False),
        enum_column("state", ReviewerApplicationState),
        Column("version", Integer, nullable=False, server_default="1"),
        ForeignKeyConstraint(
            ["user_id", "competency_id"],
            ["user_competencies.user_id", "user_competencies.competency_id"],
            name="fk_application_user_competency",
        ),
        UniqueConstraint("id", "user_id", "competency_id", "requested_grade", name="uq_application_subject_grade"),
        CheckConstraint("requested_grade <> 'PERFORMER' AND btrim(evidence) <> ''", name="ck_application_evidence"),
        CheckConstraint("version >= 1 AND user_competency_version >= 1", name="ck_application_versions"),
        Index(
            "uq_pending_application_skill_grade",
            "user_id",
            "competency_id",
            "requested_grade",
            unique=True,
            postgresql_where=text("state = 'PENDING' AND deleted_at IS NULL"),
        ),
    )
    reviewer_application_assessments = Table(
        "reviewer_application_assessments",
        metadata,
        *identity_columns(),
        Column("application_id", Uuid, nullable=False),
        Column("application_version", Integer, nullable=False),
        Column("applicant_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        enum_column("requested_grade", SkillGrade),
        Column("expert_user_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("expert_grant_id", Uuid, nullable=False),
        Column("expert_grant_version", Integer, nullable=False),
        Column("expert_competency_version", Integer, nullable=False),
        Column("expert_value", Numeric, nullable=False),
        enum_column("decision", ReadinessDecision),
        Column("feedback", String, nullable=False),
        enum_column("expert_grade", SkillGrade),
        enum_column("expert_grant_state", SkillGrantState),
        ForeignKeyConstraint(
            ["application_id", "applicant_id", "competency_id", "requested_grade"],
            [
                "reviewer_applications.id",
                "reviewer_applications.user_id",
                "reviewer_applications.competency_id",
                "reviewer_applications.requested_grade",
            ],
            name="fk_readiness_application_subject",
        ),
        ForeignKeyConstraint(
            ["expert_grant_id", "expert_user_id", "competency_id", "expert_grade"],
            ["skill_grants.id", "skill_grants.user_id", "skill_grants.competency_id", "skill_grants.grade"],
            name="fk_readiness_expert_skill",
        ),
        UniqueConstraint("application_id", "application_version", name="uq_readiness_application_version"),
        UniqueConstraint(
            "id",
            "applicant_id",
            "competency_id",
            "requested_grade",
            "expert_user_id",
            "expert_grant_id",
            "expert_grant_version",
            "decision",
            name="uq_readiness_grant_evidence",
        ),
        CheckConstraint("applicant_id <> expert_user_id", name="ck_readiness_independent"),
        CheckConstraint(
            "application_version >= 1 AND expert_grant_version >= 1 AND expert_competency_version >= 1",
            name="ck_readiness_versions",
        ),
        CheckConstraint("expert_value BETWEEN 75 AND 100", name="ck_readiness_expert_value"),
        CheckConstraint(
            "expert_grade = 'EXPERT' AND expert_grant_state = 'CONFIRMED'", name="ck_readiness_expert_rights"
        ),
        CheckConstraint("btrim(feedback) <> ''", name="ck_readiness_feedback"),
    )
    progression_contributions = Table(
        "progression_contributions",
        metadata,
        *identity_columns(),
        Column("participant_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("case_id", Uuid, ForeignKey("cases.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("review_id", Uuid, ForeignKey("review_results.id"), nullable=False),
        Column("review_revision_id", Uuid, nullable=False),
        Column("source_id", Uuid, nullable=False),
        Column("policy_version", String(100), nullable=False),
        Column("case_version", Integer, nullable=False),
        Column("score", Numeric, nullable=False),
        Column("weight", Numeric, nullable=False),
        Column("assessor_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("assessor_grant_id", Uuid, nullable=False),
        Column("assessor_grant_version", Integer, nullable=False),
        Column("assessor_competency_version", Integer, nullable=False),
        Column("expertise_value", Numeric, nullable=False),
        Column("contribution", Numeric, nullable=False),
        Column("contribution_sum_before", Numeric, nullable=False),
        Column("contribution_sum_after", Numeric, nullable=False),
        Column("displayed_value_before", Numeric, nullable=False),
        Column("displayed_value_after", Numeric, nullable=False),
        Column("effective", Boolean, nullable=False),
        Column("source_contribution_id", Uuid),
        Column("version", Integer, nullable=False, server_default="1"),
        ForeignKeyConstraint(
            ["review_revision_id", "review_id"],
            ["review_revisions.id", "review_revisions.review_id"],
            name="fk_contribution_review_revision",
        ),
        ForeignKeyConstraint(
            ["assessor_grant_id", "assessor_id", "competency_id"],
            ["skill_grants.id", "skill_grants.user_id", "skill_grants.competency_id"],
            name="fk_contribution_assessor_skill",
        ),
        UniqueConstraint(
            "id", "participant_id", "case_id", "competency_id", name="uq_contribution_correction_identity"
        ),
        ForeignKeyConstraint(
            ["source_contribution_id", "participant_id", "case_id", "competency_id"],
            [
                "progression_contributions.id",
                "progression_contributions.participant_id",
                "progression_contributions.case_id",
                "progression_contributions.competency_id",
            ],
            name="fk_contribution_previous_source",
        ),
        UniqueConstraint("source_id", "competency_id", name="uq_contribution_source_skill"),
        CheckConstraint("source_contribution_id <> id", name="ck_contribution_not_self_source"),
        CheckConstraint(
            "version >= 1 AND case_version >= 1 AND assessor_grant_version >= 1 AND assessor_competency_version >= 1",
            name="ck_contribution_versions",
        ),
        CheckConstraint("score BETWEEN 0 AND 100 AND expertise_value BETWEEN 0 AND 100", name="ck_contribution_scores"),
        CheckConstraint("weight > 0 AND weight <= 0.25", name="ck_contribution_weight"),
        CheckConstraint(
            "contribution >= 0 AND contribution < 'Infinity'::numeric "
            "AND contribution_sum_before >= 0 AND contribution_sum_before < 'Infinity'::numeric "
            "AND contribution_sum_after >= 0 AND contribution_sum_after < 'Infinity'::numeric",
            name="ck_contribution_exact_values",
        ),
        CheckConstraint(
            "displayed_value_before BETWEEN 0 AND 100 AND displayed_value_after BETWEEN 0 AND 100",
            name="ck_contribution_display_values",
        ),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_contribution_policy"),
        Index(
            "uq_effective_contribution",
            "participant_id",
            "case_id",
            "competency_id",
            unique=True,
            postgresql_where=text("effective"),
        ),
    )
    for entity, table in (
        (UserProfile, user_profiles),
        (UserCapability, user_capabilities),
        (UserCompetency, user_competencies),
        (SkillGrant, skill_grants),
        (Probation, probations),
        (ProbationSuccess, probation_successes),
        (ReviewerApplication, reviewer_applications),
        (ReviewerApplicationAssessment, reviewer_application_assessments),
        (ProgressionContribution, progression_contributions),
    ):
        if entity is SkillGrant:
            mappers.map_imperatively(
                entity,
                table,
                version_id_col=table.c.version,
                properties={
                    name: column_property(table.c[name], active_history=True)
                    for name in ("state", "confirmed_at", "revoked_at")
                },
            )
        else:
            mappers.map_imperatively(entity, table, version_id_col=table.c.get("version"))
    protect_evidence(ProbationSuccess)
    protect_evidence(ReviewerApplicationAssessment)
    protect_evidence_columns(SkillGrant, frozenset({"state", "confirmed_at", "revoked_at", "deleted_at", "version"}))
    event.listen(SkillGrant, "before_update", protect_grant_transition)
    protect_evidence_columns(ProgressionContribution, frozenset({"effective", "version"}))
