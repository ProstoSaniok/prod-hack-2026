"""Expert-only quality admission; quality jobs precede ordinary distance ordering."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.review_matching import ReviewerSkill
from src.adapters.data_gateways.tables import meta_data
from src.application.development.policy_types import POLICY_VERSION, Grade, Grant
from src.application.review.entity import AssignmentState, JobState, ReviewKind, ReviewState
from src.application.user.entity import User


def expert_skills(rights: dict[UUID, ReviewerSkill]) -> set[UUID]:
    return {
        skill_id
        for skill_id, skill in rights.items()
        if skill.authority.grade == Grade.EXPERT
        and skill.authority.grant == Grant.CONFIRMED
        and skill.authority.expert_confirmed
        and skill.authority.value >= 75
    }


class QualityMatching:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def candidate(self, user: User, rights: dict[UUID, ReviewerSkill]) -> RowMapping | None:
        jobs, submissions, requirements, projects, reviews, revisions = (
            meta_data.tables[name]
            for name in (
                "review_jobs",
                "submissions",
                "submission_competencies",
                "projects",
                "review_results",
                "review_revisions",
            )
        )
        skills = expert_skills(rights)
        if not skills:
            return None
        missing = (
            select(requirements.c.id)
            .where(
                requirements.c.submission_id == submissions.c.id,
                requirements.c.competency_id.not_in(skills),
            )
            .correlate(submissions)
            .exists()
        )
        nonempty = (
            select(requirements.c.id)
            .where(requirements.c.submission_id == submissions.c.id)
            .correlate(submissions)
            .exists()
        )
        newer = revisions.alias("newer_quality_revision")
        stale = (
            select(newer.c.id)
            .where(newer.c.review_id == reviews.c.id, newer.c.number > revisions.c.number, newer.c.deleted_at.is_(None))
            .correlate(reviews, revisions)
            .exists()
        )
        assignments = meta_data.tables["review_assignments"]
        declined = (
            select(assignments.c.id)
            .where(
                assignments.c.job_id == jobs.c.id,
                assignments.c.reviewer_id == user.id,
                assignments.c.state == AssignmentState.CANCELLED,
            )
            .exists()
        )
        statement = (
            select(jobs)
            .join(submissions, submissions.c.id == jobs.c.submission_id)
            .join(projects, projects.c.id == submissions.c.project_id)
            .join(revisions, revisions.c.id == jobs.c.reviewed_revision_id)
            .join(reviews, reviews.c.id == revisions.c.review_id)
            .where(
                jobs.c.kind == ReviewKind.QUALITY_CHECK,
                jobs.c.state == JobState.WAITING,
                ~declined,
                jobs.c.deleted_at.is_(None),
                jobs.c.policy_version == POLICY_VERSION,
                submissions.c.author_id != user.id,
                submissions.c.deleted_at.is_(None),
                projects.c.deleted_at.is_(None),
                reviews.c.reviewer_id != user.id,
                reviews.c.kind == ReviewKind.REVIEW,
                reviews.c.state == ReviewState.PENDING,
                reviews.c.submission_id == submissions.c.id,
                reviews.c.deleted_at.is_(None),
                revisions.c.submission_id == submissions.c.id,
                revisions.c.deleted_at.is_(None),
                nonempty,
                ~missing,
                ~stale,
            )
            # Relevant experts take the oldest eligible quality job first, then
            # fall back to ordinary matching. No case/author value floors apply.
            .order_by(jobs.c.created_at, jobs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=jobs)
        )
        return (await self.session.execute(statement)).mappings().one_or_none()
