"""Single-UoW primary review publication, permanent replay and real effects."""

from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import encode, row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.review_completion_authority import SqlCompletionAuthority
from src.adapters.data_gateways.review_results import metadata
from src.adapters.data_gateways.review_revision_writes import enqueue_quality, save_revision
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.review_requests import ReviewRequests
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.development.entity import SkillGrantState
from src.application.operations.idempotency import CachedResponse
from src.application.review.entity import (
    AssignmentState,
    JobState,
    ReviewKind,
    ReviewState,
)
from src.application.review.finalization import (
    PublicationEffects,
    PublicationSkill,
    ReviewPublication,
)
from src.application.user.entity import UserId


class DefaultReviewCompletion:
    def __init__(self, session: AsyncSession, effects: PublicationEffects) -> None:
        self.session, self.effects = session, effects
        self.access, self.requests = ReviewAccess(session), ReviewRequests(session)

    async def complete(self, actor: UserId, assignment_id: UUID, draft_version: int, key: str) -> CachedResponse:
        assignment, subject = await self._lock_subject(actor, assignment_id)
        user = await self.access.actor(actor, lock=True)
        if assignment["kind"] != ReviewKind.REVIEW:
            raise CaseConflictError("ASSIGNMENT_KIND_MISMATCH", "An ordinary review assignment is required")
        await self._supervised(assignment)
        rights = await self.access.matching.rights(user, lock=True)
        await self.access.authorize(user, assignment, rights)
        completed = assignment["state"] == AssignmentState.COMPLETED
        if not completed:
            await self.access.require_active(assignment)
        skills = await SqlCompletionAuthority(self.session).resolve(assignment, subject, rights)
        replay = await self.requests.begin(
            actor,
            "POST",
            f"/api/v1/review-assignments/{assignment_id}/reviews",
            key,
            encode({"expected_draft_version": draft_version}),
        )
        if replay is not None:
            return CachedResponse(status_code=200, body=replay.body, location=replay.location)
        reviews, revisions = meta_data.tables["review_results"], meta_data.tables["review_revisions"]
        existing = (
            (
                await self.session.execute(
                    select(reviews.c.id, revisions.c.source_draft_version)
                    .join(revisions, revisions.c.review_id == reviews.c.id)
                    .where(reviews.c.assignment_id == assignment_id, revisions.c.number == 1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not completed or existing["source_draft_version"] != draft_version:
                raise CaseConflictError("REVIEW_COMPLETED", "This assignment already consumed another draft version")
            return await self._finish(value(existing, "id", UUID), 200)
        if completed:
            raise CaseConflictError("ASSIGNMENT_CLOSED", "Completed assignment has no replayable review")
        return await self._publish(actor, assignment, subject, skills, draft_version)

    async def _publish(
        self,
        actor: UserId,
        assignment: dict[str, object],
        subject: dict[str, object],
        skills: tuple[PublicationSkill, ...],
        draft_version: int,
    ) -> CachedResponse:
        reviews = meta_data.tables["review_results"]
        assignment_id = value(assignment, "id", UUID)
        if subject["effective_review_id"] is not None:
            raise CaseConflictError("RESULT_CONFLICT", "This submission already has an effective review")
        now = await database_now(self.session)
        review_id = uuid4()
        await self.session.execute(
            insert(reviews).values(
                id=review_id,
                created_at=now,
                assignment_id=assignment_id,
                submission_id=subject["id"],
                reviewer_id=actor,
                kind=ReviewKind.REVIEW,
                state=ReviewState.PENDING,
            )
        )
        revision_id, content = await save_revision(
            self.session,
            assignment_id=assignment_id,
            review_id=review_id,
            submission_id=value(subject, "id", UUID),
            draft_version=draft_version,
            number=1,
            now=now,
            skills=skills,
        )
        if await self._supervised(assignment):
            await enqueue_quality(self.session, value(subject, "id", UUID), revision_id, now)
            return await self._complete_assignment(review_id, assignment)
        await self.session.execute(
            update(reviews)
            .where(reviews.c.id == review_id)
            .values(state=ReviewState.PUBLISHED, published_at=now, published_revision_id=revision_id)
        )
        submissions, projects = meta_data.tables["submissions"], meta_data.tables["projects"]
        await self.session.execute(
            update(submissions).where(submissions.c.id == subject["id"]).values(effective_review_id=review_id)
        )
        case_id = await self.session.scalar(select(projects.c.case_id).where(projects.c.id == subject["project_id"]))
        if not isinstance(case_id, UUID):
            raise TypeError("Submission has no case identity")
        await self.effects.apply(
            ReviewPublication(
                review_id=review_id,
                revision_id=revision_id,
                submission_id=value(subject, "id", UUID),
                case_id=case_id,
                case_version=value(subject, "case_version", int),
                author_id=UserId(value(subject, "author_id", UUID)),
                reviewer_id=actor,
                content=content,
                skills=skills,
                created_at=now,
                supervision_required=False,
            )
        )
        await self.session.execute(
            update(projects).where(projects.c.id == subject["project_id"]).values(updated_at=now)
        )
        return await self._complete_assignment(review_id, assignment)

    async def _complete_assignment(self, review_id: UUID, assignment: dict[str, object]) -> CachedResponse:
        # Recheck DB time after all contended skill/wallet/request locks. Lease
        # loss rolls back publication, progression, rewards and outbox together.
        await self.access.require_active(assignment)
        assignments, jobs = meta_data.tables["review_assignments"], meta_data.tables["review_jobs"]
        await self.session.execute(
            update(assignments).where(assignments.c.id == assignment["id"]).values(state=AssignmentState.COMPLETED)
        )
        await self.session.execute(
            update(jobs).where(jobs.c.id == assignment["job_id"]).values(state=JobState.COMPLETED)
        )
        return await self._finish(review_id, 201, assignment)

    async def _finish(
        self, review_id: UUID, status: int, assignment: dict[str, object] | None = None
    ) -> CachedResponse:
        reviews = meta_data.tables["review_results"]
        row = dict((await self.session.execute(select(reviews).where(reviews.c.id == review_id))).mappings().one())
        revisions = meta_data.tables["review_revisions"]
        revision = (
            (
                await self.session.execute(
                    select(revisions).where(revisions.c.review_id == review_id, revisions.c.number == 1)
                )
            )
            .mappings()
            .one()
        )
        assignments = meta_data.tables["review_assignments"]
        original = (
            (await self.session.execute(select(assignments).where(assignments.c.id == row["assignment_id"])))
            .mappings()
            .one()
        )
        # Reconstruct the original creation receipt, even after correction or
        # publication and after the request journal has expired.
        if await self._supervised(row_mapping(original)):
            row.update(state=ReviewState.PENDING, published_at=None)
        response = CachedResponse(
            status_code=status, body=metadata(row, row_mapping(revision)), location=f"/api/v1/reviews/{review_id}"
        )
        await self.requests.complete(response)
        if assignment is not None:
            await self.access.require_active(assignment)
        await self.session.commit()
        return response

    async def _lock_subject(self, actor: UserId, assignment_id: UUID) -> tuple[dict[str, object], dict[str, object]]:
        assignments, jobs, users = (meta_data.tables[name] for name in ("review_assignments", "review_jobs", "users"))
        statement = select(assignments).where(
            assignments.c.id == assignment_id, assignments.c.reviewer_id == actor, assignments.c.deleted_at.is_(None)
        )
        row = (await self.session.execute(statement)).mappings().one_or_none()
        if row is None:
            raise CaseNotFoundError
        subject = await self.access.subject(row_mapping(row))
        # A and B may review each other's independent work concurrently. Lock
        # BOTH users before any skill locks, in UUID order, to avoid AB/BA cycles.
        owners = sorted({actor, value(subject, "author_id", UUID)}, key=lambda identifier: identifier.int)
        for owner in owners:
            found = await self.session.scalar(
                select(users.c.id)
                .where(users.c.id == owner, users.c.deleted_at.is_(None))
                .with_for_update(key_share=True)
            )
            if found is None:
                raise ForbiddenError
        await self.session.execute(select(jobs.c.id).where(jobs.c.id == row["job_id"]).with_for_update())
        locked = (await self.session.execute(statement.with_for_update())).mappings().one()
        return row_mapping(locked), await self.access.subject(row_mapping(locked))

    async def _supervised(self, assignment: dict[str, object]) -> bool:
        skills = meta_data.tables["assignment_competencies"]
        snapshots = (
            (await self.session.execute(select(skills).where(skills.c.assignment_id == assignment["id"])))
            .mappings()
            .all()
        )
        requirements = await self.access.matching.requirements(value(assignment, "submission_id", UUID))
        supervised = any(row["probation_id"] is not None for row in snapshots)
        if (
            not snapshots
            or {row["competency_id"] for row in snapshots} != {row["competency_id"] for row in requirements}
            or bool(assignment["supervised"]) != supervised
            or any(
                row["deleted_at"] is not None
                or (row["grant_state"] == SkillGrantState.PROVISIONAL) != (row["probation_id"] is not None)
                or row["grant_state"] not in {SkillGrantState.PROVISIONAL, SkillGrantState.CONFIRMED}
                for row in snapshots
            )
        ):
            raise CaseConflictError("SUPERVISION_REQUIRED", "Assignment supervision evidence is incomplete")
        return supervised
