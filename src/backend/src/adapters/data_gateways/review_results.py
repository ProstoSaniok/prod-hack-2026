"""Private immutable review projections; no workspace, draft or financial reads."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import LearningViews, encode, row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.review_views import number
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseNotFoundError, CaseValidationError
from src.application.development.entity import SkillGrantState
from src.application.development.policy_types import PolicyViolationError
from src.application.development.progression_types import PROGRESSION_POLICY_VERSION
from src.application.operations.work import WorkState
from src.application.review.entity import ReviewState
from src.application.user.entity import User, UserId

MAX_RESULT_BYTES = 512 * 1024


def bounded(body: bytes) -> bytes:
    if len(body) > MAX_RESULT_BYTES:
        raise CaseValidationError("Review result exceeds 512 KiB")
    return body


def status(row: Mapping[str, object]) -> str:
    if row["state"] == ReviewState.PUBLISHED:
        return "published"
    if row["state"] == ReviewState.NEEDS_REVISION:
        return "needs_revision"
    return "pending_quality_check"


def metadata(row: Mapping[str, object], revision: Mapping[str, object]) -> bytes:
    return encode({
        "id": row["id"],
        "assignment_id": row["assignment_id"],
        "submission_id": row["submission_id"],
        "reviewer_id": row["reviewer_id"],
        "status": status(row),
        "revision_id": revision["id"],
        "revision_number": revision["number"],
        "draft_version": revision.get("source_draft_version"),
        "created_at": row["created_at"],
        "published_at": row["published_at"],
        "content_url": f"/api/v1/reviews/{value(row, 'id', UUID)}",
    })


def _records() -> Select[tuple[object, ...]]:
    reviews, submissions, projects = (meta_data.tables[name] for name in ("review_results", "submissions", "projects"))
    return (
        select(reviews, submissions.c.author_id, projects.c.participant_id.label("project_participant_id"))
        .join(submissions, submissions.c.id == reviews.c.submission_id)
        .join(projects, projects.c.id == submissions.c.project_id)
        .where(reviews.c.deleted_at.is_(None), submissions.c.deleted_at.is_(None), projects.c.deleted_at.is_(None))
    )


class DefaultReviewResultsReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.access = ReviewAccess(session)
        self.learning = LearningViews(session)

    async def reviews(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        user = await self.access.actor(actor)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise CaseValidationError("Invalid review pagination")
        reviews = meta_data.tables["review_results"]
        rows = (
            []
            if offset > 2**63 - 1
            else (
                await self.session.execute(
                    _records()
                    .where(reviews.c.reviewer_id == actor)
                    .order_by(reviews.c.created_at.desc(), reviews.c.id)
                    .limit(limit + 1)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows[:limit]:
            record = await self._record(value(row, "id", UUID))
            await self._pending_access(user, record)
            items.append(metadata(record, await self._revision(record)))
        return bounded(
            b'{"items":['
            + b",".join(items)
            + b'],"limit":'
            + encode(limit)
            + b',"offset":'
            + encode(offset)
            + b',"next_offset":'
            + encode(offset + limit if len(rows) > limit else None)
            + b"}"
        )

    async def review(self, actor: UserId, review_id: UUID) -> bytes:
        user = await self.access.actor(actor)
        row = await self._record(review_id)
        if row["reviewer_id"] == actor:
            await self._pending_access(user, row)
        elif not (
            row["state"] == ReviewState.PUBLISHED
            and row["author_id"] == actor
            and row["project_participant_id"] == actor
        ):
            # Imported here because quality projections reuse the immutable
            # result serializer without broadening author access.
            from src.adapters.data_gateways.quality_views import DefaultQualityReader  # noqa: PLC0415

            quality = DefaultQualityReader(self.session)
            revision = await quality.live_revision(user, row)
            body = await self._full(row, revision)
            await quality.live_revision(user, row, value(revision, "id", UUID))
            return body
        return await self._full(row, await self._revision(row))

    async def feedback(self, actor: UserId, submission_id: UUID) -> bytes:
        await self.access.actor(actor)
        await self._lock_published_view(actor, submission_id)
        submission = await self.learning.submission_record(actor, submission_id)
        submission_body = await self.learning.submission(actor, submission_id)
        reviews = meta_data.tables["review_results"]
        rows = (
            await self.session.execute(
                _records()
                .where(reviews.c.submission_id == submission_id, reviews.c.state == ReviewState.PUBLISHED)
                .order_by(reviews.c.created_at, reviews.c.id)
            )
        ).mappings()
        body = b"["
        for row in rows:
            record = row_mapping(row)
            item = await self._full(record, await self._revision(record))
            body = bounded(body + (b"," if len(body) > 1 else b"") + item)
        effective_id, progression = await self._progression(actor, row_mapping(submission))
        return bounded(
            b'{"submission":'
            + submission_body
            + b',"reviews":'
            + body
            + b"]"
            + b',"effective_review_id":'
            + encode(effective_id)
            + b',"progression":'
            + (progression if effective_id is not None else b"null")
            + b"}"
        )

    async def progression(self, actor: UserId, submission_id: UUID) -> bytes:
        await self.access.actor(actor)
        await self._lock_published_view(actor, submission_id)
        submission = await self.learning.submission_record(actor, submission_id)
        _, body = await self._progression(actor, row_mapping(submission))
        return body

    async def _progression(self, actor: UserId, submission: Mapping[str, object]) -> tuple[UUID | None, bytes]:
        reviews, contributions = meta_data.tables["review_results"], meta_data.tables["progression_contributions"]
        row = (
            (
                await self.session.execute(
                    _records().where(
                        reviews.c.id == submission["effective_review_id"],
                        reviews.c.submission_id == submission["id"],
                        reviews.c.state == ReviewState.PUBLISHED,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        items = []
        if row is not None:
            revision = await self._revision(row_mapping(row))
            rows = (
                await self.session.execute(
                    select(contributions)
                    .where(
                        contributions.c.participant_id == actor,
                        contributions.c.case_id == submission["case_id"],
                        contributions.c.review_id == row["id"],
                        contributions.c.review_revision_id == revision["id"],
                        contributions.c.effective.is_(True),
                        contributions.c.deleted_at.is_(None),
                    )
                    .order_by(contributions.c.competency_id, contributions.c.id)
                )
            ).mappings()
            items = [_contribution(row_mapping(item)) for item in rows]
        effective_id = value(row, "id", UUID) if row is not None else None
        return effective_id, bounded(
            encode({
                "submission_id": submission["id"],
                "effective_review_id": effective_id,
                "policy_version": PROGRESSION_POLICY_VERSION,
            })[:-1]
            + b',"contributions":['
            + b",".join(items)
            + b"]}"
        )

    async def receipt(self, review_id: UUID) -> bytes:
        """Internal metadata projection; the completion caller owns authorization."""
        row = await self._record(review_id)
        return metadata(row, await self._revision(row))

    async def internal_record(self, review_id: UUID) -> Mapping[str, object]:
        """Lock one result for coherent assembly; caller owns authorization."""
        return await self._record(review_id)

    async def internal_content(self, row: Mapping[str, object], revision: Mapping[str, object]) -> bytes:
        """Serialize an already authorized immutable revision."""
        return await self._full(row, revision)

    async def _lock_published_view(self, actor: UserId, submission_id: UUID) -> None:
        await self.learning.submission_record(actor, submission_id)
        submissions = meta_data.tables["submissions"]
        # Prevent an old effective-result pointer from being combined with a
        # newly committed review/progression while assembling the feedback page.
        await self.session.execute(
            select(submissions.c.id).where(submissions.c.id == submission_id).with_for_update(read=True)
        )

    async def _record(self, review_id: UUID) -> Mapping[str, object]:
        reviews = meta_data.tables["review_results"]
        # This is the only lock acquired by direct content readers. Writers may
        # lock user -> job -> assignment -> review; never follow this shared
        # review lock with locks on those earlier rows.
        row = (
            (
                await self.session.execute(
                    _records().where(reviews.c.id == review_id).with_for_update(read=True, of=reviews)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return row_mapping(row)

    async def _pending_access(self, user: User, row: Mapping[str, object]) -> None:
        if row["state"] == ReviewState.PUBLISHED:
            return
        # Completed results are history: never reapply the assignment's old lease.
        subject = await self.access.subject({"submission_id": row["submission_id"]})
        try:
            await self.access.matching.require(user, subject, await self.access.matching.rights(user))
        except PolicyViolationError:
            raise ForbiddenError from None

    async def _revision(self, row: Mapping[str, object]) -> Mapping[str, object]:
        revisions = meta_data.tables["review_revisions"]
        statement = select(revisions).where(
            revisions.c.review_id == row["id"],
            revisions.c.submission_id == row["submission_id"],
            revisions.c.deleted_at.is_(None),
        )
        if row["state"] == ReviewState.PUBLISHED:
            statement = statement.where(revisions.c.id == row["published_revision_id"])
        revision = (
            (await self.session.execute(statement.order_by(revisions.c.number.desc(), revisions.c.id).limit(1)))
            .mappings()
            .one_or_none()
        )
        if revision is None:
            raise CaseNotFoundError
        return row_mapping(revision)

    async def _full(self, row: Mapping[str, object], revision: Mapping[str, object]) -> bytes:
        comments, skills = meta_data.tables["review_comments"], meta_data.tables["reviewer_snapshots"]
        comment_rows = (
            await self.session.execute(
                select(comments)
                .where(comments.c.revision_id == revision["id"], comments.c.deleted_at.is_(None))
                .order_by(comments.c.created_at, comments.c.id)
            )
        ).mappings()
        skill_rows = (
            await self.session.execute(
                select(skills)
                .where(skills.c.revision_id == revision["id"], skills.c.deleted_at.is_(None))
                .order_by(skills.c.competency_id)
            )
        ).mappings()
        current = (
            encode({
                "id": revision["id"],
                "number": revision["number"],
                "draft_version": revision.get("source_draft_version"),
                "policy_version": revision["policy_version"],
                "verdict": revision["verdict"],
                "feedback": {key: revision[key] for key in ("strengths", "problems", "next_steps")},
                "comments": [
                    {
                        "body": item["text"],
                        "path": item["path"],
                        "line": item["line"],
                        "side": item["side"],
                        **({"end_line": item["end_line"]} if item["end_line"] is not None else {}),
                    }
                    for item in comment_rows
                ],
            })[:-1]
            + b',"score":'
            + number(revision["score"])
            + b',"reviewer_skills":['
        )
        current += b",".join(_reviewer_skill(row_mapping(item)) for item in skill_rows) + b"]}"
        return bounded(
            encode({
                "id": row["id"],
                "assignment_id": row["assignment_id"],
                "submission_id": row["submission_id"],
                "reviewer_id": row["reviewer_id"],
                "status": status(row),
                "created_at": row["created_at"],
                "published_at": row["published_at"],
                "pull_request_url": None,
                **await self._projection(row, revision),
            })[:-1]
            + b',"current_revision":'
            + current
            + b"}"
        )

    async def _projection(self, row: Mapping[str, object], revision: Mapping[str, object]) -> dict[str, object]:
        result: dict[str, object] = {
            "projection_status": "not_eligible",
            "gitea_review_id": None,
            "gitea_comment_id": None,
        }
        if row["state"] != ReviewState.PUBLISHED or row["published_revision_id"] != revision["id"]:
            return result
        messages, receipts = (meta_data.tables[name] for name in ("outbox_messages", "review_git_projections"))
        record = (
            (
                await self.session.execute(
                    select(messages.c.state, receipts.c.external_comment_id, receipts.c.completed_at)
                    .outerjoin(
                        receipts,
                        (receipts.c.message_id == messages.c.id)
                        & (receipts.c.review_id == row["id"])
                        & (receipts.c.revision_id == revision["id"]),
                    )
                    .where(
                        messages.c.topic == "review.published",
                        messages.c.subject_id == row["id"],
                        messages.c.source_key == str(revision["id"]),
                        messages.c.revision == revision["number"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        result["projection_status"] = "pending"
        if record is not None:
            if record["state"] == WorkState.SUCCEEDED and record["completed_at"] is not None:
                result.update(projection_status="published", gitea_comment_id=record["external_comment_id"])
            elif record["state"] == WorkState.RETRY_WAIT:
                result["projection_status"] = "retryable_failure"
            elif record["state"] in {WorkState.NEEDS_ATTENTION, WorkState.SUCCEEDED}:
                result["projection_status"] = "needs_attention"
        return result


def _reviewer_skill(row: Mapping[str, object]) -> bytes:
    state = row["grant_state"]
    if state not in {SkillGrantState.CONFIRMED, SkillGrantState.PROVISIONAL}:
        raise CaseNotFoundError
    return (
        encode({
            "competency_id": row["competency_id"],
            "grade": row["grade"],
            "grant": "confirmed" if state == SkillGrantState.CONFIRMED else "supervised",
            "grant_id": row["grant_id"],
            "grant_version": row["grant_version"],
            "user_competency_version": row["user_competency_version"],
        })[:-1]
        + b',"value":'
        + number(row["value"])
        + b"}"
    )


def _contribution(row: Mapping[str, object]) -> bytes:
    fields = {
        key: row[key]
        for key in (
            "competency_id",
            "review_id",
            "case_version",
            "assessor_id",
            "assessor_grant_version",
            "assessor_competency_version",
        )
    }
    fields["revision_id"] = row["review_revision_id"]
    numeric = b",".join(
        encode(key) + b":" + number(row[key])
        for key in (
            "score",
            "weight",
            "expertise_value",
            "contribution",
            "contribution_sum_before",
            "contribution_sum_after",
            "displayed_value_before",
            "displayed_value_after",
        )
    )
    return encode(fields)[:-1] + b"," + numeric + b"}"
