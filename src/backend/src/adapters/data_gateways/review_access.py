from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.quality_matching import expert_skills
from src.adapters.data_gateways.review_matching import ReviewMatching, ReviewerSkill
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.development.policy_types import POLICY_VERSION, PolicyViolationError
from src.application.review.entity import AssignmentState, JobState, ReviewKind, ReviewState
from src.application.user.capability import ProductCapability
from src.application.user.entity import User, UserId


class ReviewAccess:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.matching = ReviewMatching(session)

    async def owner(self, actor: UserId, *, lock: bool = False) -> User:
        """Account ownership is enough to inspect or release one's allocation metadata."""
        users = meta_data.tables["users"]
        statement = (
            select(User)
            .where(users.c.id == actor, users.c.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        if lock:
            # Serialize capacity without blocking other transactions' FK checks.
            statement = statement.with_for_update(key_share=True)
        user = await self.session.scalar(statement)
        if user is None:
            raise ForbiddenError
        return user

    async def actor(self, actor: UserId, *, lock: bool = False) -> User:
        user = await self.owner(actor, lock=lock)
        capabilities = meta_data.tables["user_capabilities"]
        grants = select(capabilities.c.id).where(
            capabilities.c.user_id == actor,
            capabilities.c.capability == ProductCapability.PARTICIPANT,
            capabilities.c.deleted_at.is_(None),
            capabilities.c.revoked_at.is_(None),
        )
        if lock:
            grants = grants.with_for_update(read=True)
        if await self.session.scalar(grants) is None:
            raise ForbiddenError
        return user

    async def assignment(
        self, actor: UserId, assignment_id: UUID, *, lock: bool = False, active: bool = True
    ) -> dict[str, object]:
        table, jobs = meta_data.tables["review_assignments"], meta_data.tables["review_jobs"]
        statement = select(table).where(
            table.c.id == assignment_id, table.c.reviewer_id == actor, table.c.deleted_at.is_(None)
        )
        row = (await self.session.execute(statement)).mappings().one_or_none()
        if row is None:
            raise CaseNotFoundError
        if lock:
            # All assignment transitions take job then assignment.
            await self.session.execute(select(jobs.c.id).where(jobs.c.id == row["job_id"]).with_for_update())
            row = (await self.session.execute(statement.with_for_update())).mappings().one()
        result = row_mapping(row)
        if active:
            await self.require_active(result)
        return result

    async def require_active(self, assignment: dict[str, object]) -> None:
        if assignment["kind"] not in {ReviewKind.REVIEW, ReviewKind.QUALITY_CHECK}:
            raise CaseConflictError("ASSIGNMENT_KIND", "Recheck assignments are not supported")
        if assignment["state"] != AssignmentState.ACTIVE:
            raise CaseConflictError("ASSIGNMENT_CLOSED", "Review assignment is no longer active")

    async def subject(self, assignment: dict[str, object]) -> dict[str, object]:
        submissions, projects = meta_data.tables["submissions"], meta_data.tables["projects"]
        row = (
            (
                await self.session.execute(
                    select(submissions)
                    .join(projects, projects.c.id == submissions.c.project_id)
                    .where(
                        submissions.c.id == assignment["submission_id"],
                        submissions.c.deleted_at.is_(None),
                        projects.c.deleted_at.is_(None),
                        submissions.c.operation_id.is_not(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return row_mapping(row)

    async def authorize(
        self, user: User, assignment: dict[str, object], rights: dict[UUID, ReviewerSkill]
    ) -> dict[str, object]:
        subject = await self.subject(assignment)
        if assignment["kind"] == ReviewKind.QUALITY_CHECK:
            await self.quality(user, assignment, rights)
            return subject
        if assignment["kind"] != ReviewKind.REVIEW:
            raise CaseConflictError("ASSIGNMENT_KIND", "Recheck assignments are not supported")
        try:
            await self.matching.require(user, subject, rights)
        except PolicyViolationError:
            raise ForbiddenError from None
        return subject

    async def quality(
        self, user: User, assignment: dict[str, object], rights: dict[UUID, ReviewerSkill]
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Current expert authorization for one exact pending review revision.

        Callers needing exact-read serialization acquire their user/authority and
        job locks first. This check itself never upgrades to exclusive row locks.
        """
        subject = await self.subject(assignment)
        rows = await self.matching.requirements(value(subject, "id", UUID))
        skills = expert_skills(rights)
        if (
            assignment["reviewer_id"] != user.id
            or subject["author_id"] == user.id
            or not rows
            or any(row["competency_id"] not in skills for row in rows)
        ):
            raise CaseNotFoundError
        jobs, reviews, revisions = (
            meta_data.tables[name] for name in ("review_jobs", "review_results", "review_revisions")
        )
        job = (
            (await self.session.execute(select(jobs).where(jobs.c.id == assignment["job_id"]))).mappings().one_or_none()
        )
        revision = None
        if job is not None:
            revision = (
                (await self.session.execute(select(revisions).where(revisions.c.id == job["reviewed_revision_id"])))
                .mappings()
                .one_or_none()
            )
        review = None
        if revision is not None:
            review = (
                (await self.session.execute(select(reviews).where(reviews.c.id == revision["review_id"])))
                .mappings()
                .one_or_none()
            )
        if review is not None and review["reviewer_id"] == user.id:
            raise CaseNotFoundError
        if (
            assignment["kind"] != ReviewKind.QUALITY_CHECK
            or job is None
            or job["kind"] != ReviewKind.QUALITY_CHECK
            or job["submission_id"] != subject["id"]
            or job["deleted_at"] is not None
            or job["policy_version"] != POLICY_VERSION
            or job["state"] not in {JobState.WAITING, JobState.CLAIMED}
            or revision is None
            or revision["submission_id"] != subject["id"]
            or revision["deleted_at"] is not None
            or review is None
            or review["submission_id"] != subject["id"]
            or review["kind"] != ReviewKind.REVIEW
            or review["deleted_at"] is not None
        ):
            raise CaseConflictError("STATE_CONFLICT", "Quality assignment no longer targets a pending review")
        latest = await self.session.scalar(
            select(revisions.c.id)
            .where(revisions.c.review_id == review["id"], revisions.c.deleted_at.is_(None))
            .order_by(revisions.c.number.desc(), revisions.c.id)
            .limit(1)
        )
        if review["state"] != ReviewState.PENDING or latest != revision["id"]:
            raise CaseConflictError("STATE_CONFLICT", "Quality assignment targets a stale review revision")
        return row_mapping(review), row_mapping(revision)

    async def owned(self, actor: UserId, assignment_id: UUID) -> tuple[dict[str, object], dict[str, object]]:
        assignment = await self.assignment(actor, assignment_id, active=False)
        try:
            user = await self.actor(actor)
            subject = await self.authorize(user, assignment, await self.matching.rights(user))
        except ForbiddenError:
            if assignment["kind"] == ReviewKind.QUALITY_CHECK:
                raise CaseNotFoundError from None
            raise
        await self.require_active(assignment)
        return assignment, subject

    async def editable(
        self, actor: UserId, assignment_id: UUID, *, lock: bool = False
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Live work or the original completed ordinary review needing correction."""
        # Keep the same user -> authority -> job -> assignment order as claim.
        assignment = await self.assignment(actor, assignment_id, active=False)
        try:
            user = await self.actor(actor, lock=lock)
        except ForbiddenError:
            if assignment["kind"] == ReviewKind.QUALITY_CHECK:
                raise CaseNotFoundError from None
            raise
        rights = await self.matching.rights(user, lock=lock)
        assignment = await self.assignment(actor, assignment_id, lock=lock, active=False)
        subject = await self.authorize(user, assignment, rights)
        if assignment["state"] != AssignmentState.COMPLETED or assignment["kind"] != ReviewKind.REVIEW:
            await self.require_active(assignment)
            return assignment, subject
        reviews = meta_data.tables["review_results"]
        statement = select(reviews.c.id).where(
            reviews.c.assignment_id == assignment_id,
            reviews.c.reviewer_id == actor,
            reviews.c.submission_id == subject["id"],
            reviews.c.kind == ReviewKind.REVIEW,
            reviews.c.state == ReviewState.NEEDS_REVISION,
            reviews.c.deleted_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update(read=True)
        if await self.session.scalar(statement) is None:
            raise CaseConflictError("ASSIGNMENT_CLOSED", "Completed review is not awaiting correction")
        return assignment, subject
