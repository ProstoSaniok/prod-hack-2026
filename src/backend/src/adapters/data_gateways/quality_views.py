"""Private quality projections with no allocation, lease repair or GET writes."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import encode, row_mapping
from src.adapters.data_gateways.quality_matching import expert_skills
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.review_matching import ReviewerSkill
from src.adapters.data_gateways.review_results import DefaultReviewResultsReader, bounded
from src.adapters.data_gateways.review_views import ReviewViews, number
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError, CaseValidationError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.development.policy_types import PolicyViolationError
from src.application.review.entity import AssignmentState, ReviewKind, ReviewState
from src.application.user.entity import User, UserId


def pagination(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise CaseValidationError("Invalid quality pagination")


def page(items: list[bytes], limit: int, offset: int, *, more: bool) -> bytes:
    return bounded(
        b'{"items":['
        + b",".join(items)
        + b'],"limit":'
        + encode(limit)
        + b',"offset":'
        + encode(offset)
        + b',"next_offset":'
        + encode(offset + limit if more else None)
        + b"}"
    )


class DefaultQualityReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.access = ReviewAccess(session)
        self.results = DefaultReviewResultsReader(session)

    async def source(self, actor: UserId, assignment_id: UUID) -> bytes:
        row, revision = await self._source(actor, assignment_id)
        body = await self.results.internal_content(row, revision)
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        subject = await self.access.subject(assignment)
        workspace = await ReviewViews(self.session).workspace(assignment, subject)
        # Lease reaping does not need the result row. Recheck after assembling
        # content, without acquiring a job/assignment lock behind the result.
        await self._source(actor, assignment_id)
        return bounded(body[:-1] + b',"workspace":' + workspace + b"}")

    async def _source(self, actor: UserId, assignment_id: UUID) -> tuple[Mapping[str, object], Mapping[str, object]]:
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        user = await self._actor(actor)
        rights = await self.access.matching.rights(user)
        if assignment["kind"] != ReviewKind.QUALITY_CHECK:
            try:
                await self.access.matching.require(user, await self.access.subject(assignment), rights)
            except PolicyViolationError:
                raise CaseNotFoundError from None
            raise CaseConflictError("ASSIGNMENT_KIND_MISMATCH", "This assignment is not a quality check")
        # Authorize before disclosing state, then lock only the result. Repeat
        # all authority/assignment/revision checks after waiting for a writer.
        review, _ = await self._quality(user, assignment, rights)
        row = await self.results.internal_record(value(review, "id", UUID))
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        user = await self._actor(actor)
        review, revision = await self._quality(user, assignment, await self.access.matching.rights(user))
        if review["id"] != row["id"]:
            raise CaseConflictError("STALE_REVISION", "Quality assignment targets a stale review revision")
        return row, revision

    async def _quality(
        self, user: User, assignment: dict[str, object], rights: dict[UUID, ReviewerSkill]
    ) -> tuple[dict[str, object], dict[str, object]]:
        try:
            result = await self.access.quality(user, assignment, rights)
        except CaseConflictError:
            # Authorization ran inside quality() before any state checks. An
            # expired/closed lease takes precedence over a stale target.
            await self._active(assignment)
            raise CaseConflictError("STALE_REVISION", "Quality assignment targets a stale review revision") from None
        await self._active(assignment)
        return result

    async def _active(self, assignment: dict[str, object]) -> None:
        try:
            await self.access.require_active(assignment)
        except CaseConflictError:
            if assignment["state"] not in {AssignmentState.ACTIVE, AssignmentState.EXPIRED}:
                raise CaseConflictError("ASSIGNMENT_NOT_ACTIVE", "Quality assignment is no longer active") from None
            raise

    async def live_revision(
        self, user: User, row: Mapping[str, object], revision_id: UUID | None = None
    ) -> Mapping[str, object]:
        """Internal exact-live-assignment authorization; the result is already locked."""
        await self._expert(user, row)
        assignments, jobs, revisions = (
            meta_data.tables[name] for name in ("review_assignments", "review_jobs", "review_revisions")
        )
        statement = (
            select(assignments.c.id)
            .join(jobs, jobs.c.id == assignments.c.job_id)
            .join(revisions, revisions.c.id == jobs.c.reviewed_revision_id)
            .where(
                assignments.c.reviewer_id == user.id,
                assignments.c.kind == ReviewKind.QUALITY_CHECK,
                assignments.c.state == AssignmentState.ACTIVE,
                assignments.c.deleted_at.is_(None),
                revisions.c.review_id == row["id"],
            )
            .order_by(assignments.c.created_at.desc(), assignments.c.id)
            .limit(1)
        )
        if revision_id is not None:
            statement = statement.where(revisions.c.id == revision_id)
        assignment_id = await self.session.scalar(statement)
        if not isinstance(assignment_id, UUID):
            raise CaseNotFoundError
        try:
            current, revision = await self._source(user.id, assignment_id)
        except CaseConflictError:
            raise CaseNotFoundError from None
        if current["id"] != row["id"] or (revision_id is not None and revision["id"] != revision_id):
            raise CaseNotFoundError
        return revision

    async def assessment(self, actor: UserId, review_id: UUID, assessment_id: UUID) -> bytes:
        user = await self._actor(actor)
        row = await self.results.internal_record(review_id)
        table, revisions = meta_data.tables["quality_assessments"], meta_data.tables["review_revisions"]
        assessment = (
            (
                await self.session.execute(
                    select(table)
                    .join(revisions, revisions.c.id == table.c.revision_id)
                    .where(
                        table.c.id == assessment_id,
                        table.c.deleted_at.is_(None),
                        revisions.c.review_id == review_id,
                        revisions.c.submission_id == row["submission_id"],
                        revisions.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if assessment is None:
            raise CaseNotFoundError
        if row["reviewer_id"] == actor:
            await self._reviewer(user, row)
        else:
            if assessment["assessor_id"] != actor:
                raise CaseNotFoundError
            await self._expert(user, row)
        return await self._assessment(row_mapping(assessment), review_id)

    async def assessments(self, actor: UserId, review_id: UUID, *, limit: int, offset: int) -> bytes:
        user = await self._actor(actor)
        pagination(limit, offset)
        row = await self.results.internal_record(review_id)
        table, revisions = meta_data.tables["quality_assessments"], meta_data.tables["review_revisions"]
        statement = (
            select(table)
            .join(revisions, revisions.c.id == table.c.revision_id)
            .where(
                table.c.deleted_at.is_(None),
                revisions.c.review_id == review_id,
                revisions.c.submission_id == row["submission_id"],
                revisions.c.deleted_at.is_(None),
            )
        )
        if row["reviewer_id"] == actor:
            await self._reviewer(user, row)
        else:
            await self._expert(user, row)
            statement = statement.where(table.c.assessor_id == actor)
            if await self.session.scalar(statement.with_only_columns(table.c.id).limit(1)) is None:
                # An assigned expert may see an empty history before assessing;
                # an unrelated expert cannot discover even review metadata.
                await self.live_revision(user, row)
        rows = (
            []
            if offset > 2**63 - 1
            else (
                await self.session.execute(
                    statement.order_by(table.c.created_at.desc(), table.c.id).limit(limit + 1).offset(offset)
                )
            )
            .mappings()
            .all()
        )
        items = [assessment_metadata(row_mapping(item), review_id) for item in rows[:limit]]
        return page(items, limit, offset, more=len(rows) > limit)

    async def revision(self, actor: UserId, review_id: UUID, revision_id: UUID) -> bytes:
        user = await self._actor(actor)
        row = await self.results.internal_record(review_id)
        table = meta_data.tables["review_revisions"]
        revision = (
            (
                await self.session.execute(
                    select(table).where(
                        table.c.id == revision_id,
                        table.c.review_id == review_id,
                        table.c.submission_id == row["submission_id"],
                        table.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if revision is None:
            raise CaseNotFoundError
        published = row["state"] == ReviewState.PUBLISHED and row["published_revision_id"] == revision_id
        if row["reviewer_id"] == actor:
            if not published:
                await self._reviewer(user, row)
        elif row["author_id"] == actor and row["project_participant_id"] == actor:
            if not published:
                raise CaseNotFoundError
        else:
            await self.live_revision(user, row, revision_id)
        body = await self.results.internal_content(row, row_mapping(revision))
        if row["reviewer_id"] != actor and row["author_id"] != actor:
            await self.live_revision(user, row, revision_id)
        return body

    async def probation(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        await self.access.actor(actor)
        pagination(limit, offset)
        table, successes = meta_data.tables["probations"], meta_data.tables["probation_successes"]
        confirmed = (
            select(func.count(successes.c.id))
            .where(
                successes.c.probation_id == table.c.id,
                successes.c.reviewer_id == actor,
                successes.c.competency_id == table.c.competency_id,
                successes.c.deleted_at.is_(None),
            )
            .correlate(table)
            .scalar_subquery()
            .label("confirmed_reviews")
        )
        rows = (
            []
            if offset > 2**63 - 1
            else (
                await self.session.execute(
                    select(table, confirmed)
                    .where(table.c.user_id == actor, table.c.deleted_at.is_(None))
                    .order_by(table.c.created_at.desc(), table.c.id)
                    .limit(limit + 1)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )
        items = []
        for item in rows[:limit]:
            record = row_mapping(item)
            items.append(
                encode({
                    "id": record["id"],
                    "competency_id": record["competency_id"],
                    "grade": record["grade"],
                    "grant_id": record["skill_grant_id"],
                    "required_successes": record["required_successes"],
                    "confirmed_reviews": record["confirmed_reviews"],
                    "remaining": max(
                        0, value(record, "required_successes", int) - value(record, "confirmed_reviews", int)
                    ),
                    "completed_at": record["completed_at"],
                    "version": record["version"],
                })
            )
        return page(items, limit, offset, more=len(rows) > limit)

    async def _actor(self, actor: UserId) -> User:
        try:
            return await self.access.actor(actor)
        except ForbiddenError:
            raise CaseNotFoundError from None

    async def _reviewer(self, user: User, row: Mapping[str, object]) -> None:
        subject = await self.access.subject({"submission_id": row["submission_id"]})
        try:
            await self.access.matching.require(user, subject, await self.access.matching.rights(user))
        except PolicyViolationError:
            raise ForbiddenError from None

    async def _expert(self, user: User, row: Mapping[str, object]) -> None:
        if row["author_id"] == user.id or row["reviewer_id"] == user.id:
            raise CaseNotFoundError
        requirements = await self.access.matching.requirements(value(row, "submission_id", UUID))
        skills = expert_skills(await self.access.matching.rights(user))
        if not requirements or any(item["competency_id"] not in skills for item in requirements):
            raise CaseNotFoundError

    async def _assessment(self, row: Mapping[str, object], review_id: UUID) -> bytes:
        feedback = value(row, "feedback", str)
        if not feedback.strip() or len(feedback) > 6000:
            raise CaseValidationError("Quality feedback exceeds the content bound")
        table = meta_data.tables["assessor_snapshots"]
        skills = (
            await self.session.execute(
                select(table)
                .where(table.c.assessment_id == row["id"], table.c.deleted_at.is_(None))
                .order_by(table.c.competency_id, table.c.id)
            )
        ).mappings()
        return bounded(
            encode({
                "id": row["id"],
                "review_id": review_id,
                "assignment_id": row["assignment_id"],
                "revision_id": row["revision_id"],
                "assessor_id": row["assessor_id"],
                "decision": row["decision"],
                "feedback": feedback,
                "created_at": row["created_at"],
                "content_url": f"/api/v1/reviews/{review_id}/quality-assessments/{value(row, 'id', UUID)}",
            })[:-1]
            + b',"score":'
            + number(row["quality_score"])
            + b',"assessor_skills":['
            + b",".join(_assessor_skill(row_mapping(skill)) for skill in skills)
            + b"]}"
        )


def assessment_metadata(row: Mapping[str, object], review_id: UUID) -> bytes:
    fields = {key: row[key] for key in ("id", "assignment_id", "revision_id", "assessor_id", "decision", "created_at")}
    fields.update(
        review_id=review_id, content_url=f"/api/v1/reviews/{review_id}/quality-assessments/{value(row, 'id', UUID)}"
    )
    return encode(fields)[:-1] + b',"score":' + number(row["quality_score"]) + b"}"


def _assessor_skill(row: Mapping[str, object]) -> bytes:
    if row["grade"] != SkillGrade.EXPERT or row["grant_state"] != SkillGrantState.CONFIRMED:
        raise CaseNotFoundError
    return (
        encode({
            "competency_id": row["competency_id"],
            "grade": row["grade"],
            "grant": "confirmed",
            "grant_id": row["grant_id"],
            "grant_version": row["grant_version"],
            "user_competency_version": row["user_competency_version"],
        })[:-1]
        + b',"value":'
        + number(row["value"])
        + b"}"
    )
