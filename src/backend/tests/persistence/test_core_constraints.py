from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from src.adapters.data_gateways.tables import meta_data
from src.application.review.entity import ReviewDraft, ReviewRevision
from tests.persistence.helpers import add_revision, claim, insert, seed_review, seed_subject


@pytest.mark.parametrize(
    ("required_value", "weight"),
    [("-1", "0.1"), ("101", "0.1"), ("NaN", "0.1"), ("80", "0"), ("80", "0.2500001"), ("80", "NaN")],
)
def test_database_checks_requirement_and_weight(core_engine: Engine, required_value: str, weight: str) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        with pytest.raises(IntegrityError, match="ck_case_"):
            insert(
                connection,
                "case_competencies",
                case_id=subject.case,
                competency_id=subject.competency,
                required_value=Decimal(required_value),
                weight=Decimal(weight),
            )


def test_database_preserves_unrounded_decimal_and_unique_case_skill(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        values = {
            "case_id": subject.case,
            "competency_id": subject.competency,
            "required_value": Decimal(80),
            "weight": Decimal("0.123456789123456789123456789"),
        }
        row_id = insert(connection, "case_competencies", **values)
        table = meta_data.tables["case_competencies"]
        assert connection.scalar(select(table.c.weight).where(table.c.id == row_id)) == values["weight"]
        with pytest.raises(IntegrityError, match="uq_case_competency"):
            insert(connection, "case_competencies", **values)


def test_project_identity_survives_soft_delete(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        projects = meta_data.tables["projects"]
        connection.execute(
            projects.update().where(projects.c.id == subject.project).values(deleted_at=projects.c.created_at)
        )
        repository = insert(
            connection,
            "repositories",
            owner_id=subject.author,
            provider="test-only",
            external_id=str(uuid4()),
            purpose="WORKSPACE",
        )
        with pytest.raises(IntegrityError, match="uq_project_participant_case"):
            insert(
                connection,
                "projects",
                participant_id=subject.author,
                case_id=subject.case,
                case_version=1,
                repository_id=repository,
                working_branch="work",
                state="ACTIVE",
            )


def test_only_one_submission_per_project(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        submissions = meta_data.tables["submissions"]
        row = connection.execute(select(submissions).where(submissions.c.id == subject.submission)).mappings().one()
        with pytest.raises(IntegrityError, match="submissions_project_id_key"):
            insert(
                connection,
                "submissions",
                project_id=subject.project,
                repository_id=subject.repository,
                retained_reference_id=row["retained_reference_id"],
                case_version=1,
            )


def test_primary_quality_and_recheck_jobs_coexist(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        _, revision = seed_review(connection, subject)
        quality = insert(
            connection,
            "review_jobs",
            submission_id=subject.submission,
            kind="QUALITY_CHECK",
            reviewed_revision_id=revision,
            state="WAITING",
            policy_version="p01",
        )
        claim(connection, subject, job_id=quality, kind="QUALITY_CHECK", reviewer_id=subject.expert)
        for _ in range(2):
            insert(
                connection,
                "review_jobs",
                submission_id=subject.submission,
                kind="RECHECK",
                state="WAITING",
                policy_version="p01",
            )
        jobs = meta_data.tables["review_jobs"]
        assert len(connection.execute(select(jobs.c.id).where(jobs.c.submission_id == subject.submission)).all()) == 4


def test_active_job_claim_unique_but_expired_history_does_not_block(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        expired = claim(connection, subject, state="EXPIRED")
        active = claim(connection, subject)
        assert expired != active
        with pytest.raises(IntegrityError, match="uq_assignment_active_job"):
            claim(connection, subject, reviewer_id=subject.expert)


def test_active_reviewer_capacity_is_distinct_from_job_identity(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        claim(connection, subject)
        job = insert(
            connection,
            "review_jobs",
            submission_id=subject.submission,
            kind="RECHECK",
            state="WAITING",
            policy_version="p01",
        )
        with pytest.raises(IntegrityError, match="uq_assignment_active_reviewer"):
            claim(connection, subject, job_id=job, kind="RECHECK")


def test_concurrent_claims_have_one_winner(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
    barrier = Barrier(2)

    def compete(*, second_reviewer: bool) -> bool:
        try:
            with core_engine.begin() as connection:
                barrier.wait(timeout=10)
                claim(connection, subject, reviewer_id=subject.expert if second_reviewer else subject.reviewer)
        except IntegrityError as error:
            if "uq_assignment_active_job" not in str(error):
                raise
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(compete, second_reviewer=False)
        second = executor.submit(compete, second_reviewer=True)
        assert sorted([first.result(timeout=15), second.result(timeout=15)]) == [False, True]


def test_quality_completion_must_target_assigned_revision(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        review, first_revision = seed_review(connection, subject)
        second_revision = add_revision(connection, subject, review, 2)
        job = insert(
            connection,
            "review_jobs",
            submission_id=subject.submission,
            kind="QUALITY_CHECK",
            reviewed_revision_id=first_revision,
            state="CLAIMED",
            policy_version="p01",
        )
        assignment = claim(connection, subject, job_id=job, kind="QUALITY_CHECK", reviewer_id=subject.expert)
        with pytest.raises(IntegrityError, match="fk_quality_assigned_revision"):
            insert(
                connection,
                "quality_assessments",
                assignment_id=assignment,
                job_id=job,
                submission_id=subject.submission,
                revision_id=second_revision,
                assessor_id=subject.expert,
                quality_score=90,
                decision="CONFIRMED",
                feedback="Sound reasoning",
                kind="QUALITY_CHECK",
            )


def test_quality_confirmation_below_seventy_fails(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        _, revision = seed_review(connection, subject)
        job = insert(
            connection,
            "review_jobs",
            submission_id=subject.submission,
            kind="QUALITY_CHECK",
            reviewed_revision_id=revision,
            state="WAITING",
            policy_version="p01",
        )
        assignment = claim(connection, subject, job_id=job, kind="QUALITY_CHECK", reviewer_id=subject.expert)
        with pytest.raises(IntegrityError, match="ck_quality_confirmation"):
            insert(
                connection,
                "quality_assessments",
                assignment_id=assignment,
                job_id=job,
                submission_id=subject.submission,
                revision_id=revision,
                assessor_id=subject.expert,
                quality_score=Decimal("69.99"),
                decision="CONFIRMED",
                feedback="Improve reasoning",
                kind="QUALITY_CHECK",
            )


def test_effective_result_cannot_point_to_another_submission(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        first = seed_subject(connection)
        second = seed_subject(connection)
        review, _ = seed_review(connection, second)
        submissions = meta_data.tables["submissions"]
        with pytest.raises(IntegrityError, match="fk_submission_effective_review"):
            connection.execute(
                submissions.update().where(submissions.c.id == first.submission).values(effective_review_id=review)
            )


def test_assignment_cannot_change_job_subject(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        first = seed_subject(connection)
        second = seed_subject(connection)
        with pytest.raises(IntegrityError, match="fk_assignment_job_subject"):
            claim(connection, replace(first, submission=second.submission))


def test_ready_reference_must_belong_to_private_reference_repository(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        reference_repository = insert(
            connection,
            "repositories",
            owner_id=subject.owner,
            provider="test-only",
            external_id=str(uuid4()),
            purpose="REFERENCE",
        )
        retained = insert(
            connection,
            "retained_references",
            repository_id=subject.repository,
            external_id="different",
            commit_id="opaque-2",
        )
        with pytest.raises(IntegrityError, match="fk_reference_ready_repository"):
            insert(
                connection,
                "reference_solutions",
                case_id=subject.case,
                repository_id=reference_repository,
                ready_reference_id=retained,
                ready_at=datetime.now(UTC),
            )


def test_reference_cannot_reuse_participant_workspace(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        with pytest.raises(IntegrityError, match="fk_reference_repository_purpose"):
            insert(connection, "reference_solutions", case_id=subject.case, repository_id=subject.repository)


def test_submission_cannot_pin_another_workspace_object(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        first = seed_subject(connection)
        second = seed_subject(connection)
        submissions = meta_data.tables["submissions"]
        other_ref = connection.scalar(
            select(submissions.c.retained_reference_id).where(submissions.c.id == second.submission)
        )
        with pytest.raises(IntegrityError, match="fk_submission_retained_repository"):
            connection.execute(
                submissions.update().where(submissions.c.id == first.submission).values(retained_reference_id=other_ref)
            )


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_orm_revision_evidence_is_write_once(core_engine: Engine, operation: str) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        _, revision_id = seed_review(connection, subject)
    with Session(core_engine) as session:
        revision = session.get(ReviewRevision, revision_id)
        assert revision is not None
        if operation == "update":
            revision.score = Decimal(100)
        else:
            session.delete(revision)
        with pytest.raises(ValueError, match="write-once evidence"):
            session.flush()


def test_comment_side_without_line_is_invalid(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        _, revision = seed_review(connection, subject)
        with pytest.raises(IntegrityError, match="ck_comment_position"):
            insert(connection, "review_comments", revision_id=revision, text="Check this", path="a.py", side="NEW")


def test_draft_initial_version_and_optimistic_updates(core_engine: Engine) -> None:
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        assignment = claim(connection, subject)
        draft_id = insert(
            connection, "review_drafts", assignment_id=assignment, strengths="", problems="", next_steps=""
        )
    with Session(core_engine) as first, Session(core_engine) as second:
        original = first.get(ReviewDraft, draft_id)
        stale = second.get(ReviewDraft, draft_id)
        assert original is not None
        assert stale is not None
        assert original.version == 1
        assert original.score is None
        original.strengths = "Updated draft"
        first.commit()
        assert original.version == 2
        stale.problems = "Conflicting stale update"
        with pytest.raises(StaleDataError):
            second.commit()
