"""Fresh per-skill authority and ordinary queue selection, without allocating work."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.catalog.management import CaseConflictError
from src.application.development.entity import (
    Probation,
    SkillGrade,
    SkillGrant,
    SkillGrantState,
    UserCompetency,
    has_expert_rights,
    has_review_rights,
)
from src.application.development.policy import require_solution_reviewer
from src.application.development.policy_types import (
    POLICY_VERSION,
    CaseSkillInput,
    Grade,
    Grant,
    SkillAuthorityInput,
)
from src.application.review.entity import AssignmentState, JobState, ReviewKind
from src.application.user.entity import User
from src.application.wallet.entity import ReservationState

GRADES = tuple(SkillGrade)


@dataclass(frozen=True)
class ReviewerSkill:
    competency: UserCompetency
    grant: SkillGrant
    authority: SkillAuthorityInput
    probation_id: UUID | None = None

    @property
    def rank(self) -> int:
        return GRADES.index(self.competency.grade)


class ReviewMatching:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rights(self, user: User, *, lock: bool = False) -> dict[UUID, ReviewerSkill]:
        skills, grants = meta_data.tables["user_competencies"], meta_data.tables["skill_grants"]
        statement = (
            select(UserCompetency, SkillGrant)
            .join(
                SkillGrant, and_(grants.c.user_id == skills.c.user_id, grants.c.competency_id == skills.c.competency_id)
            )
            .where(
                skills.c.user_id == user.id,
                skills.c.deleted_at.is_(None),
                grants.c.deleted_at.is_(None),
                grants.c.state.in_([SkillGrantState.CONFIRMED, SkillGrantState.PROVISIONAL]),
                grants.c.revoked_at.is_(None),
            )
            .order_by(skills.c.competency_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update(read=True, of=[skills.c.id, grants.c.id])
        records = (await self.session.execute(statement)).tuples().all()
        probations = meta_data.tables["probations"]
        probation_statement = (
            select(Probation)
            .where(
                probations.c.user_id == user.id,
                probations.c.completed_at.is_(None),
                probations.c.deleted_at.is_(None),
            )
            .order_by(probations.c.competency_id, probations.c.id)
            .execution_options(populate_existing=True)
        )
        if lock:
            probation_statement = probation_statement.with_for_update(read=True)
        live_probations = {
            probation.skill_grant_id: probation for probation in await self.session.scalars(probation_statement)
        }
        result: dict[UUID, ReviewerSkill] = {}
        for competency, grant in records:
            probation = live_probations.get(grant.id) if grant.state == SkillGrantState.PROVISIONAL else None
            supervised = (
                probation is not None
                and probation.user_id == user.id
                and probation.competency_id == competency.competency_id
                and probation.grade == competency.grade == grant.grade
                and probation.required_successes == 3
                and probation.epoch >= 1
                and probation.version >= 1
            )
            if not has_review_rights(user, competency.competency_id, competency, grant, supervised=supervised):
                continue
            expert = has_expert_rights(user, competency.competency_id, competency, grant)
            if competency.grade == SkillGrade.EXPERT and (not expert or competency.displayed_value < 75):
                continue
            result[competency.competency_id] = ReviewerSkill(
                competency,
                grant,
                SkillAuthorityInput(
                    user_id=user.id,
                    skill_id=competency.competency_id,
                    value=competency.displayed_value,
                    grade=Grade(competency.grade.value),
                    grant=Grant.SUPERVISED if supervised else Grant.CONFIRMED,
                    expert_confirmed=expert,
                ),
                probation_id=probation.id if supervised and probation is not None else None,
            )
        return result

    async def requirements(self, submission_id: UUID) -> list[RowMapping]:
        table = meta_data.tables["submission_competencies"]
        return list(
            (
                await self.session.execute(
                    select(table).where(table.c.submission_id == submission_id).order_by(table.c.competency_id)
                )
            ).mappings()
        )

    async def require(
        self, user: User, submission: Mapping[str, object], rights: dict[UUID, ReviewerSkill]
    ) -> list[RowMapping]:
        rows = await self.requirements(value(submission, "id", UUID))
        requirements = tuple(
            CaseSkillInput(
                skill_id=row["competency_id"],
                required_value=row["required_value"],
                author_grade=Grade(row["author_grade"].value),
            )
            for row in rows
        )
        require_solution_reviewer(
            user.id,
            value(submission, "author_id", UUID),
            requirements,
            tuple(skill.authority for skill in rights.values()),
        )
        return rows

    async def candidate(
        self,
        user: User,
        rights: dict[UUID, ReviewerSkill],
        queue: Literal["all", "priority", "ordinary", "aged"] = "all",
    ) -> RowMapping | None:
        jobs, submissions, requirements, projects = (
            meta_data.tables[name] for name in ("review_jobs", "submissions", "submission_competencies", "projects")
        )
        # SQL is an admission/order optimization. The canonical policy is checked
        # again for the selected subject, under the same fresh authority locks.
        eligible = or_(
            false(),
            *(
                and_(
                    requirements.c.competency_id == skill_id,
                    requirements.c.required_value <= skill.authority.value,
                    requirements.c.required_value < (50 if skill.rank == 1 else 75 if skill.rank == 2 else 101),
                    requirements.c.author_grade.in_([
                        grade for grade in GRADES if min(GRADES.index(grade) + 1, 3) <= skill.rank
                    ]),
                )
                for skill_id, skill in rights.items()
            ),
        )
        missing = (
            select(requirements.c.id)
            .where(requirements.c.submission_id == submissions.c.id, ~eligible)
            .correlate(submissions)
            .exists()
        )
        nonempty = (
            select(requirements.c.id)
            .where(requirements.c.submission_id == submissions.c.id)
            .correlate(submissions)
            .exists()
        )
        case_floor = case((requirements.c.required_value >= 75, 3), (requirements.c.required_value >= 50, 2), else_=1)
        author_floor = case(
            {grade: min(index + 1, 3) for index, grade in enumerate(GRADES)}, value=requirements.c.author_grade, else_=3
        )
        reviewer_rank = (
            case(
                {skill_id: skill.rank for skill_id, skill in rights.items()},
                value=requirements.c.competency_id,
                else_=0,
            )
            if rights
            else 0
        )
        distance = (
            select(func.sum(reviewer_rank - func.greatest(case_floor, author_floor)))
            .where(requirements.c.submission_id == submissions.c.id)
            .correlate(submissions)
            .scalar_subquery()
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
            .where(
                jobs.c.kind == ReviewKind.REVIEW,
                jobs.c.state == JobState.WAITING,
                ~declined,
                jobs.c.deleted_at.is_(None),
                jobs.c.policy_version == POLICY_VERSION,
                submissions.c.author_id != user.id,
                submissions.c.deleted_at.is_(None),
                projects.c.deleted_at.is_(None),
                nonempty,
                ~missing,
            )
            .order_by(distance, jobs.c.created_at, jobs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=jobs)
        )
        holds = meta_data.tables["wallet_reservations"]
        if queue != "all":
            priority = (
                select(holds.c.id).where(holds.c.job_id == jobs.c.id, holds.c.state == ReservationState.HELD).exists()
            )
            statement = statement.where(priority if queue == "priority" else ~priority)
            if queue == "aged":
                statement = statement.where(jobs.c.created_at <= await database_now(self.session) - timedelta(hours=24))
        for _ in range(100):
            row = (await self.session.execute(statement)).mappings().one_or_none()
            if row is None or queue == "all":
                return row
            # A purchase/cancellation can commit between the SELECT snapshot and
            # acquiring the job lock without updating the job itself. Recheck the
            # class with a fresh snapshot now that its mutation fence is held.
            held = await self.session.scalar(
                select(holds.c.id).where(holds.c.job_id == row["id"], holds.c.state == ReservationState.HELD)
            )
            if (held is not None) == (queue == "priority"):
                return row
        raise CaseConflictError("OPERATION_IN_PROGRESS", "Priority queue changed during allocation")
