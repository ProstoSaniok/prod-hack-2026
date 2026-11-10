from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.entity import RetainedReference
from src.application.development.entity import (
    ProbationSuccess,
    ProgressionContribution,
    ReviewerApplicationAssessment,
    SkillGrant,
    SkillGrantState,
    UserCompetency,
)
from src.application.project.entity import SubmissionCompetency
from src.application.review.entity import (
    AssessorSnapshot,
    AssignmentCompetency,
    QualityAssessment,
    ReviewComment,
    ReviewDraftId,
    ReviewerSnapshot,
)
from tests.persistence.helpers import assess_revision, claim, insert, seed_grant, seed_review, seed_subject
from tests.support.development_constraints import contribution_values, readiness_values, seed_application


def seed_evidence(connection: Connection) -> dict[str, tuple[type[object], UUID, str, object]]:
    subject = seed_subject(connection)
    reviewer_grant = seed_grant(connection, subject.reviewer, subject.competency)
    expert_grant = seed_grant(connection, subject.expert, subject.competency, expert=True)
    probation = insert(
        connection,
        "probations",
        user_id=subject.reviewer,
        competency_id=subject.competency,
        grade="REVIEW_MINUS",
        skill_grant_id=reviewer_grant,
        epoch=1,
    )
    review, revision = seed_review(connection, subject)
    assessment = assess_revision(connection, subject, revision, expert_grant)
    success = insert(
        connection,
        "probation_successes",
        probation_id=probation,
        reviewer_id=subject.reviewer,
        competency_id=subject.competency,
        review_id=review,
        review_revision_id=revision,
        quality_assessment_id=assessment,
        expert_user_id=subject.expert,
        expert_grant_id=expert_grant,
        expert_grant_version=1,
        quality_score=90,
        quality_decision="CONFIRMED",
    )
    application = seed_application(connection, subject)
    readiness = insert(
        connection, "reviewer_application_assessments", **readiness_values(subject, application, expert_grant)
    )
    contribution = insert(
        connection, "progression_contributions", **contribution_values(subject, review, revision, expert_grant)
    )
    snapshot = insert(
        connection,
        "reviewer_snapshots",
        revision_id=revision,
        competency_id=subject.competency,
        value=30,
        grade="REVIEW_MINUS",
        grant_id=reviewer_grant,
        grant_version=1,
        grant_state="PROVISIONAL",
        user_competency_version=1,
    )
    submission_skill = insert(
        connection,
        "submission_competencies",
        submission_id=subject.submission,
        competency_id=subject.competency,
        required_value=80,
        weight=Decimal("0.1"),
        author_value=0,
        author_grade="PERFORMER",
    )
    assignment = claim(connection, subject)
    assigned_skill = insert(
        connection,
        "assignment_competencies",
        assignment_id=assignment,
        competency_id=subject.competency,
        grant_id=reviewer_grant,
        grant_version=1,
        value=30,
        grade="REVIEW_MINUS",
        grant_state="PROVISIONAL",
        probation_id=probation,
        user_competency_version=1,
    )
    retained = insert(
        connection,
        "retained_references",
        repository_id=subject.repository,
        external_id="extra-test-evidence",
        commit_id="opaque-object",
    )
    table = meta_data.tables["assessor_snapshots"]
    assessor_snapshot = connection.scalar(select(table.c.id).where(table.c.assessment_id == assessment))
    assert isinstance(assessor_snapshot, UUID)
    return {
        "retained": (RetainedReference, retained, "commit_id", "changed-object"),
        "submission_skill": (SubmissionCompetency, submission_skill, "author_value", Decimal(10)),
        "assignment_skill": (AssignmentCompetency, assigned_skill, "value", Decimal(40)),
        "reviewer_snapshot": (ReviewerSnapshot, snapshot, "value", Decimal(40)),
        "quality": (QualityAssessment, assessment, "feedback", "Changed feedback"),
        "assessor_snapshot": (AssessorSnapshot, assessor_snapshot, "value", Decimal(95)),
        "success": (ProbationSuccess, success, "quality_score", Decimal(95)),
        "readiness": (ReviewerApplicationAssessment, readiness, "feedback", "Changed feedback"),
        "contribution": (ProgressionContribution, contribution, "contribution", Decimal(20)),
        "grant": (SkillGrant, reviewer_grant, "evidence", "Changed evidence"),
    }


@pytest.mark.parametrize(
    "record",
    [
        "retained",
        "submission_skill",
        "assignment_skill",
        "reviewer_snapshot",
        "quality",
        "success",
        "readiness",
        "contribution",
        "grant",
        "assessor_snapshot",
    ],
)
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_other_evidence_is_write_once(core_engine: Engine, record: str, operation: str) -> None:
    with core_engine.begin() as connection:
        entity, row_id, field, changed_value = seed_evidence(connection)[record]
    with Session(core_engine) as session:
        evidence = session.get(entity, row_id)
        assert evidence is not None
        if operation == "update":
            setattr(evidence, field, changed_value)
        else:
            session.delete(evidence)
        with pytest.raises(ValueError, match="write-once evidence"):
            session.flush()


@pytest.mark.parametrize("operation", ["delete", "reparent", "edit"])
def test_revision_comment_cannot_be_deleted_reparented_or_edited(core_engine: Engine, operation: str) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        _, revision = seed_review(connection, subject)
        comment_id = insert(connection, "review_comments", revision_id=revision, text="Original feedback")
        assignment = claim(connection, subject)
        draft = insert(connection, "review_drafts", assignment_id=assignment, strengths="", problems="", next_steps="")
    with Session(core_engine) as session:
        comment = session.get(ReviewComment, comment_id)
        assert comment is not None
        if operation == "delete":
            session.delete(comment)
        elif operation == "reparent":
            session.expire(comment)
            comment.revision_id = None
            comment.draft_id = ReviewDraftId(draft)
        else:
            comment.text = "Changed feedback"
        with pytest.raises(ValueError, match="write-once evidence"):
            session.flush()


def test_draft_comments_remain_editable_and_removable(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        assignment = claim(connection, subject)
        draft = insert(connection, "review_drafts", assignment_id=assignment, strengths="", problems="", next_steps="")
        comment_id = insert(connection, "review_comments", draft_id=draft, text="Draft comment")
    with Session(core_engine) as session:
        comment = session.get(ReviewComment, comment_id)
        assert comment is not None
        comment.text = "Edited draft comment"
        session.commit()
        assert comment.text == "Edited draft comment"
        session.delete(comment)
        session.commit()
        assert session.get(ReviewComment, comment_id) is None


@pytest.mark.parametrize("field", ["confirmed_at", "revoked_at"])
def test_grant_historical_timestamps_cannot_be_replaced(core_engine: Engine, field: str) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        grant_id = seed_grant(connection, subject.reviewer, subject.competency, expert=True)
    with Session(core_engine) as session:
        grant = session.get(SkillGrant, grant_id)
        assert grant is not None
        if field == "revoked_at":
            grant.state = SkillGrantState.REVOKED
            grant.revoked_at = datetime.now(UTC)
            session.commit()
        session.expire(grant)
        setattr(grant, field, datetime.now(UTC))
        with pytest.raises(ValueError, match="timestamps are write-once"):
            session.flush()


@pytest.mark.parametrize("record", ["assignment_skill", "reviewer_snapshot", "assessor_snapshot"])
def test_snapshot_provenance_versions_round_trip_and_are_historical(core_engine: Engine, record: str) -> None:
    with core_engine.begin() as connection:
        entity, row_id, _, _ = seed_evidence(connection)[record]
    with Session(core_engine) as session:
        snapshot = session.get(entity, row_id)
        assert isinstance(snapshot, (AssignmentCompetency, ReviewerSnapshot, AssessorSnapshot))
        assert snapshot.grant_version == 1
        assert snapshot.user_competency_version == 1
        with pytest.raises(ValueError, match="competency version must be positive"):
            replace(snapshot, user_competency_version=0)
        grant = session.get(SkillGrant, snapshot.grant_id)
        assert grant is not None
        table = meta_data.tables["user_competencies"]
        competency = session.scalar(
            select(UserCompetency).where(table.c.user_id == grant.user_id, table.c.competency_id == grant.competency_id)
        )
        assert competency is not None
        competency.contribution_sum += Decimal(1)
        grant.state = SkillGrantState.REVOKED
        grant.revoked_at = datetime.now(UTC)
        session.commit()
        assert competency.version == 2
        assert grant.version == 2
        session.expunge_all()
        historical = session.get(entity, row_id)
        assert isinstance(historical, (AssignmentCompetency, ReviewerSnapshot, AssessorSnapshot))
        assert historical.grant_version == 1
        assert historical.user_competency_version == 1


@pytest.mark.parametrize("record", ["assignment_skill", "reviewer_snapshot", "assessor_snapshot"])
@pytest.mark.parametrize("version", [0, -1])
def test_database_requires_positive_snapshot_competency_version(core_engine: Engine, record: str, version: int) -> None:
    with core_engine.begin() as connection:
        _, row_id, _, _ = seed_evidence(connection)[record]
        table_name = {
            "assignment_skill": "assignment_competencies",
            "reviewer_snapshot": "reviewer_snapshots",
            "assessor_snapshot": "assessor_snapshots",
        }[record]
        table = meta_data.tables[table_name]
        with pytest.raises(IntegrityError, match="competency_version"):
            connection.execute(table.update().where(table.c.id == row_id).values(user_competency_version=version))
