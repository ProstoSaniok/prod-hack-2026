from uuid import UUID, uuid4

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_work import database_now
from src.application.development.policy_types import POLICY_VERSION
from src.application.review.entity import JobState, ReviewKind


async def enqueue_submission(session: AsyncSession, submission_id: UUID) -> None:
    jobs = meta_data.tables["review_jobs"]
    await session.execute(
        insert(jobs)
        .values(
            id=uuid4(),
            created_at=await database_now(session),
            submission_id=submission_id,
            kind=ReviewKind.REVIEW,
            state=JobState.WAITING,
            policy_version=POLICY_VERSION,
        )
        .on_conflict_do_nothing(index_elements=[jobs.c.submission_id], index_where=jobs.c.kind == ReviewKind.REVIEW)
    )


async def reconcile_submissions(session: AsyncSession) -> None:
    """Bounded compatibility repair on explicit claim POST, never on a GET."""
    submissions, jobs = meta_data.tables["submissions"], meta_data.tables["review_jobs"]
    exists = (
        select(jobs.c.id).where(jobs.c.submission_id == submissions.c.id, jobs.c.kind == ReviewKind.REVIEW).exists()
    )
    candidates = (
        select(submissions.c.id, submissions.c.created_at)
        .where(submissions.c.deleted_at.is_(None), submissions.c.operation_id.is_not(None), ~exists)
        .order_by(submissions.c.created_at, submissions.c.id)
        .limit(100)
        .subquery()
    )
    await session.execute(
        insert(jobs)
        .from_select(
            ["id", "created_at", "submission_id", "kind", "state", "policy_version"],
            select(
                func.gen_random_uuid(),
                candidates.c.created_at,
                candidates.c.id,
                literal(ReviewKind.REVIEW, type_=jobs.c.kind.type),
                literal(JobState.WAITING, type_=jobs.c.state.type),
                literal(POLICY_VERSION),
            ),
        )
        .on_conflict_do_nothing(index_elements=[jobs.c.submission_id], index_where=jobs.c.kind == ReviewKind.REVIEW)
    )
