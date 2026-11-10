from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Connection

from src.adapters.data_gateways.tables import meta_data


def insert(connection: Connection, table: str, **values: object) -> UUID:
    row_id = uuid4()
    data: dict[str, object] = {"id": row_id, "created_at": datetime.now(UTC), **values}
    connection.execute(meta_data.tables[table].insert().values(**data))
    value = data["id"]
    if not isinstance(value, UUID):
        raise TypeError("Test row identity must be a UUID")
    return value


@dataclass(frozen=True)
class Subject:
    owner: UUID
    author: UUID
    reviewer: UUID
    expert: UUID
    case: UUID
    competency: UUID
    repository: UUID
    project: UUID
    submission: UUID
    job: UUID


def seed_subject(connection: Connection) -> Subject:
    owner, author, reviewer, expert = (insert(connection, "users", role="USER") for _ in range(4))
    competency = insert(connection, "competencies", name=f"SQL-{uuid4()}", description="SQL")
    case = insert(
        connection,
        "cases",
        owner_id=owner,
        title="SQL case",
        description="Build a schema",
        expected_result="A working schema",
        status="DRAFT",
    )
    repository = insert(
        connection, "repositories", owner_id=author, provider="test-only", external_id=str(uuid4()), purpose="WORKSPACE"
    )
    retained = insert(
        connection,
        "retained_references",
        repository_id=repository,
        external_id="retained-1",
        commit_id="opaque-object-1",
    )
    project = insert(
        connection,
        "projects",
        participant_id=author,
        case_id=case,
        case_version=1,
        repository_id=repository,
        working_branch="work",
        state="SUBMITTED",
    )
    submission = insert(
        connection,
        "submissions",
        project_id=project,
        repository_id=repository,
        retained_reference_id=retained,
        case_version=1,
    )
    job = insert(
        connection, "review_jobs", submission_id=submission, kind="REVIEW", state="WAITING", policy_version="p01"
    )
    return Subject(owner, author, reviewer, expert, case, competency, repository, project, submission, job)


def claim(connection: Connection, subject: Subject, **overrides: object) -> UUID:
    values: dict[str, object] = {
        "job_id": subject.job,
        "submission_id": subject.submission,
        "kind": "REVIEW",
        "reviewer_id": subject.reviewer,
        "lease_expires_at": datetime.now(UTC) + timedelta(hours=24),
        "supervised": True,
        "state": "ACTIVE",
        **overrides,
    }
    return insert(connection, "review_assignments", **values)


def seed_review(connection: Connection, subject: Subject) -> tuple[UUID, UUID]:
    assignment = claim(connection, subject, state="COMPLETED")
    review = insert(
        connection,
        "review_results",
        assignment_id=assignment,
        submission_id=subject.submission,
        reviewer_id=subject.reviewer,
        kind="REVIEW",
        state="AWAITING_QUALITY",
    )
    revision = add_revision(connection, subject, review, 1)
    return review, revision


def add_revision(connection: Connection, subject: Subject, review: UUID, number: int) -> UUID:
    return insert(
        connection,
        "review_revisions",
        review_id=review,
        submission_id=subject.submission,
        number=number,
        policy_version="p01",
        score=80,
        verdict="APPROVED",
        strengths="Sound design",
        problems="Missing indexes",
        next_steps="Add indexes",
    )


def seed_grant(connection: Connection, user: UUID, competency: UUID, *, expert: bool = False) -> UUID:
    grade = "EXPERT" if expert else "REVIEW_MINUS"
    insert(
        connection,
        "user_competencies",
        user_id=user,
        competency_id=competency,
        contribution_sum=Decimal(90) if expert else Decimal(30),
        displayed_value=Decimal(90) if expert else Decimal(30),
        grade=grade,
    )
    return insert(
        connection,
        "skill_grants",
        user_id=user,
        competency_id=competency,
        user_competency_version=1,
        grade=grade,
        state="CONFIRMED" if expert else "PROVISIONAL",
        evidence_kind="SEED" if expert else "PROGRESSION",
        evidence="Explicit test-only skill evidence",
        confirmed_at=datetime.now(UTC) if expert else None,
        seed_reference="test-only-seed" if expert else None,
        readiness_decision="CONFIRMED",
        confirmed_by_grade="EXPERT",
    )


def assess_revision(connection: Connection, subject: Subject, revision: UUID, expert_grant: UUID) -> UUID:
    job = insert(
        connection,
        "review_jobs",
        submission_id=subject.submission,
        kind="QUALITY_CHECK",
        reviewed_revision_id=revision,
        state="COMPLETED",
        policy_version="p01",
    )
    assignment = claim(
        connection, subject, job_id=job, kind="QUALITY_CHECK", reviewer_id=subject.expert, state="COMPLETED"
    )
    assessment = insert(
        connection,
        "quality_assessments",
        assignment_id=assignment,
        job_id=job,
        submission_id=subject.submission,
        revision_id=revision,
        assessor_id=subject.expert,
        quality_score=90,
        decision="CONFIRMED",
        feedback="Sound reasoning",
        kind="QUALITY_CHECK",
    )
    insert(
        connection,
        "assessor_snapshots",
        assessment_id=assessment,
        competency_id=subject.competency,
        value=90,
        grant_id=expert_grant,
        grant_version=1,
        user_competency_version=1,
        grade="EXPERT",
        grant_state="CONFIRMED",
    )
    return assessment
