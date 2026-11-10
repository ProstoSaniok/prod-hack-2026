"""Lossless author evidence, independent of mutable current grant/skill versions."""

from sqlalchemy import Column, DateTime, Integer, MetaData, String, UniqueConstraint, Uuid

from src.adapters.data_gateways.core_tables import enum_column
from src.adapters.data_gateways.learning_tables import bind, check, map_added_columns
from src.application.development.entity import SkillGrade, SkillGrantEvidenceKind, SkillGrantState
from src.application.project.entity import SubmissionCompetency


def extend_submission_snapshots(metadata: MetaData) -> None:
    table = metadata.tables["submission_competencies"]
    for name in (
        "content_version_id",
        "author_id",
        "author_grant_id",
        "author_readiness_assessment_id",
        "author_confirmed_by_user_id",
        "author_confirmed_by_grant_id",
    ):
        table.append_column(Column(name, Uuid))
    for name in (
        "author_user_competency_version",
        "author_grant_version",
        "author_grant_user_competency_version",
        "author_confirmed_by_grant_version",
    ):
        table.append_column(Column(name, Integer))
        check(table, f"ck_snapshot_{name}", f"{name} > 0")
    for name in ("author_snapshotted_at", "author_grant_confirmed_at", "author_grant_revoked_at"):
        table.append_column(Column(name, DateTime(timezone=True)))
    for name in ("author_grant_evidence", "author_seed_reference"):
        table.append_column(Column(name, String))
    for name, enum in (
        ("author_grant_state", SkillGrantState),
        ("author_grant_grade", SkillGrade),
        ("author_grant_evidence_kind", SkillGrantEvidenceKind),
    ):
        table.append_column(enum_column(name, enum, nullable=True))
    bind(
        table,
        "fk_author_snapshot_submission",
        ("submission_id", "author_id", "content_version_id"),
        "submissions",
        ("id", "author_id", "content_version_id"),
    )
    bind(
        table,
        "fk_author_snapshot_requirement",
        ("content_version_id", "competency_id", "required_value", "weight"),
        "case_content_competencies",
        ("content_version_id", "competency_id", "required_value", "weight"),
    )
    bind(
        table,
        "fk_author_snapshot_skill",
        ("author_id", "competency_id"),
        "user_competencies",
        ("user_id", "competency_id"),
    )
    bind(
        table,
        "fk_author_snapshot_grant",
        ("author_grant_id", "author_id", "competency_id", "author_grant_grade"),
        "skill_grants",
        ("id", "user_id", "competency_id", "grade"),
    )
    bind(
        table,
        "fk_author_snapshot_confirming_grant",
        ("author_confirmed_by_grant_id", "author_confirmed_by_user_id", "competency_id"),
        "skill_grants",
        ("id", "user_id", "competency_id"),
    )
    # The immutable assessment binds the historical expert grant VERSION and actor.
    bind(
        table,
        "fk_author_snapshot_readiness",
        (
            "author_readiness_assessment_id",
            "author_id",
            "competency_id",
            "author_grant_grade",
            "author_confirmed_by_user_id",
            "author_confirmed_by_grant_id",
            "author_confirmed_by_grant_version",
        ),
        "reviewer_application_assessments",
        (
            "id",
            "applicant_id",
            "competency_id",
            "requested_grade",
            "expert_user_id",
            "expert_grant_id",
            "expert_grant_version",
        ),
    )
    assessment = metadata.tables["reviewer_application_assessments"]
    assessment.append_constraint(
        UniqueConstraint(
            "id",
            "applicant_id",
            "competency_id",
            "requested_grade",
            "expert_user_id",
            "expert_grant_id",
            "expert_grant_version",
            name="uq_readiness_snapshot_provenance",
        )
    )
    check(
        table,
        "ck_author_snapshot_presence",
        "(content_version_id IS NULL AND author_id IS NULL AND "
        "author_user_competency_version IS NULL AND author_snapshotted_at IS NULL AND author_grant_id IS NULL) OR "
        "(content_version_id IS NOT NULL AND author_id IS NOT NULL AND author_user_competency_version IS NOT NULL "
        "AND author_snapshotted_at IS NOT NULL)",
    )
    grant_fields = (
        "author_grant_version",
        "author_grant_state",
        "author_grant_grade",
        "author_grant_user_competency_version",
        "author_grant_evidence_kind",
        "author_grant_evidence",
    )
    provenance = (
        "author_readiness_assessment_id",
        "author_confirmed_by_user_id",
        "author_confirmed_by_grant_id",
        "author_confirmed_by_grant_version",
        "author_seed_reference",
        "author_grant_confirmed_at",
        "author_grant_revoked_at",
    )
    check(
        table,
        "ck_author_grant_presence",
        "(author_grant_id IS NULL AND "
        + " AND ".join(f"{name} IS NULL" for name in (*grant_fields, *provenance))
        + ") OR (author_grant_id IS NOT NULL AND "
        + " AND ".join(f"{name} IS NOT NULL" for name in grant_fields)
        + ")",
    )
    check(
        table,
        "ck_author_grant_state",
        "author_grant_id IS NULL OR (author_grant_grade <> 'PERFORMER' AND "
        "btrim(author_grant_evidence) <> '' "
        "AND (author_grant_state = 'REVOKED') = (author_grant_revoked_at IS NOT NULL) "
        "AND (author_grant_state <> 'CONFIRMED' OR author_grant_confirmed_at IS NOT NULL))",
    )
    check(
        table,
        "ck_author_grant_provenance",
        "author_grant_id IS NULL OR "
        "(author_grant_evidence_kind = 'SEED' AND author_seed_reference IS NOT NULL "
        "AND btrim(author_seed_reference) <> '' AND author_readiness_assessment_id IS NULL "
        "AND ((author_confirmed_by_user_id IS NULL AND author_confirmed_by_grant_id IS NULL "
        "AND author_confirmed_by_grant_version IS NULL) OR "
        "(author_grant_grade <> 'EXPERT' AND author_confirmed_by_user_id IS NOT NULL "
        "AND author_confirmed_by_grant_id IS NOT NULL AND author_confirmed_by_grant_version IS NOT NULL "
        "AND author_confirmed_by_grant_version >= 1 AND author_confirmed_by_user_id <> author_id "
        "AND author_confirmed_by_grant_id <> author_grant_id))) OR "
        "(author_grant_evidence_kind = 'INDEPENDENT_READINESS' AND author_readiness_assessment_id IS NOT NULL "
        "AND author_confirmed_by_user_id IS NOT NULL AND author_confirmed_by_grant_id IS NOT NULL "
        "AND author_confirmed_by_grant_version IS NOT NULL AND author_confirmed_by_user_id <> author_id "
        "AND author_confirmed_by_grant_id <> author_grant_id AND author_seed_reference IS NULL) OR "
        # Preserve the earlier probation provenance shapes as well as master's
        # graduated progression snapshots; the state CHECK gates confirmed_at.
        "(author_grant_evidence_kind = 'PROGRESSION' AND author_seed_reference IS NULL "
        "AND author_readiness_assessment_id IS NULL AND "
        "((author_confirmed_by_user_id IS NULL AND author_confirmed_by_grant_id IS NULL "
        "AND author_confirmed_by_grant_version IS NULL) OR "
        "(author_grant_grade <> 'EXPERT' AND author_confirmed_by_user_id IS NOT NULL "
        "AND author_confirmed_by_grant_id IS NOT NULL AND author_confirmed_by_grant_version IS NOT NULL "
        "AND author_confirmed_by_grant_version >= 1 AND author_confirmed_by_user_id <> author_id "
        "AND author_confirmed_by_grant_id <> author_grant_id)) "
        "AND NOT (author_grant_grade = 'EXPERT' AND author_grant_confirmed_at IS NOT NULL))",
    )
    map_added_columns(SubmissionCompetency, table)
