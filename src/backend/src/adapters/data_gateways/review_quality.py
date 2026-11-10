"""Revision-pinned quality decisions and private corrections in one caller-owned UoW."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import encode, row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.review_matching import ReviewerSkill
from src.adapters.data_gateways.review_revision_writes import (
    enqueue_quality,
    final_content,
    publication_skill,
    save_revision,
)
from src.adapters.data_gateways.review_views import number
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.review_requests import ReviewRequests
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.development.policy import assess_review_quality
from src.application.development.policy_types import (
    PolicyViolationError,
    QualityAssessmentInput,
    QualityAssignmentInput,
    QualityDecision as PolicyDecision,
    ReviewSnapshot,
)
from src.application.operations.idempotency import CachedResponse
from src.application.review.entity import AssignmentState, JobState, QualityDecision, ReviewKind, ReviewState
from src.application.review.finalization import (
    PublicationConfirmation,
    PublicationEffects,
    PublicationSkill,
    ReviewPublication,
)
from src.application.review.quality import QualityInput
from src.application.user.entity import UserId


class DefaultReviewQuality:
    def __init__(self, session: AsyncSession, effects: PublicationEffects) -> None:
        self.session, self.effects = session, effects
        self.access, self.requests = ReviewAccess(session), ReviewRequests(session)

    async def assess(self, actor: UserId, review_id: UUID, command: QualityInput, key: str) -> CachedResponse:
        assignment, review, subject = await self._lock(actor, review_id, command.assignment_id)
        expert = await self.access.actor(actor, lock=True)
        rights = await self.access.matching.rights(expert, lock=True)
        reviewer = await self.access.actor(UserId(value(review, "reviewer_id", UUID)), lock=True)
        try:
            await self.access.matching.require(
                reviewer, subject, await self.access.matching.rights(reviewer, lock=True)
            )
        except PolicyViolationError:
            raise ForbiddenError from None
        revision = await self._revision(review_id, command.revision_id)
        await self._authorize_quality(actor, assignment, review, subject, revision, command, rights)
        completed = assignment["state"] == AssignmentState.COMPLETED
        if not completed:
            await self.access.require_active(assignment)
        payload = (
            encode({
                "assignment_id": command.assignment_id,
                "revision_id": command.revision_id,
                "decision": command.decision,
                "feedback": command.feedback,
            })[:-1]
            + b',"score":'
            + number(command.score)
            + b"}"
        )
        replay = await self.requests.begin(
            actor, "POST", f"/api/v1/reviews/{review_id}/quality-assessments", key, payload
        )
        if replay is not None:
            return replace(replay, status_code=200)
        assessments = meta_data.tables["quality_assessments"]
        existing = (
            (
                await self.session.execute(
                    select(assessments).where(assessments.c.assignment_id == command.assignment_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if (
                not completed
                or existing["revision_id"] != command.revision_id
                or existing["decision"] != command.decision
                or existing["quality_score"] != command.score
                or existing["feedback"] != command.feedback
            ):
                raise CaseConflictError(
                    "ASSESSMENT_COMPLETED", "This quality assignment already has a different decision"
                )
            return await self._finish(assessment_receipt(review_id, row_mapping(existing), 200))
        if completed:
            raise CaseConflictError("ASSIGNMENT_NOT_ACTIVE", "Quality assignment is completed")
        await self._require_current(review, revision)
        return await self._decide(actor, assignment, review, subject, revision, command, rights)

    async def _authorize_quality(
        self,
        actor: UserId,
        assignment: dict[str, object],
        review: dict[str, object],
        subject: dict[str, object],
        revision: dict[str, object],
        command: QualityInput,
        rights: dict[UUID, ReviewerSkill],
    ) -> None:
        # Canonical all-skill independent expert policy precedes state disclosure.
        skills = await self.access.matching.requirements(value(subject, "id", UUID))
        try:
            assess_review_quality(
                ReviewSnapshot(
                    review_id=value(review, "id", UUID),
                    revision_id=command.revision_id,
                    author_id=value(subject, "author_id", UUID),
                    reviewer_id=value(review, "reviewer_id", UUID),
                    skill_ids=frozenset(value(row, "competency_id", UUID) for row in skills),
                ),
                QualityAssessmentInput(
                    assignment=QualityAssignmentInput(
                        assignment_id=command.assignment_id,
                        review_id=value(review, "id", UUID),
                        revision_id=value(revision, "id", UUID),
                        expert_id=actor,
                        active=True,
                    ),
                    decision=PolicyDecision(command.decision.value),
                    quality_score=command.score,
                    feedback=command.feedback,
                ),
                tuple(skill.authority for skill in rights.values()),
            )
        except PolicyViolationError:
            raise CaseNotFoundError from None
        if assignment["kind"] != ReviewKind.QUALITY_CHECK:
            raise CaseConflictError("ASSIGNMENT_KIND_MISMATCH", "A quality assignment is required")
        jobs = meta_data.tables["review_jobs"]
        pinned = await self.session.scalar(select(jobs.c.reviewed_revision_id).where(jobs.c.id == assignment["job_id"]))
        if pinned != command.revision_id:
            raise CaseConflictError("STALE_REVISION", "Quality assignment targets a different revision")

    async def _decide(
        self,
        actor: UserId,
        assignment: dict[str, object],
        review: dict[str, object],
        subject: dict[str, object],
        revision: dict[str, object],
        command: QualityInput,
        rights: dict[UUID, ReviewerSkill],
    ) -> CachedResponse:
        now, assessment_id = await database_now(self.session), uuid4()
        requirements = await self.access.matching.requirements(value(subject, "id", UUID))
        experts = tuple(
            publication_skill(row_mapping(row), rights[value(row, "competency_id", UUID)]) for row in requirements
        )
        record = {
            "id": assessment_id,
            "created_at": now,
            "assignment_id": assignment["id"],
            "job_id": assignment["job_id"],
            "submission_id": subject["id"],
            "revision_id": revision["id"],
            "assessor_id": actor,
            "quality_score": command.score,
            "decision": command.decision,
            "feedback": command.feedback,
            "kind": ReviewKind.QUALITY_CHECK,
        }
        await self.session.execute(insert(meta_data.tables["quality_assessments"]).values(**record))
        for skill in experts:
            await self.session.execute(
                insert(meta_data.tables["assessor_snapshots"]).values(
                    id=uuid4(),
                    created_at=now,
                    assessment_id=assessment_id,
                    competency_id=skill.competency_id,
                    value=skill.reviewer_value,
                    grade=skill.reviewer_grade,
                    grant_state=skill.grant_state,
                    grant_id=skill.grant_id,
                    grant_version=skill.grant_version,
                    user_competency_version=skill.competency_version,
                )
            )
        if command.decision == QualityDecision.CONFIRMED:
            await self._publish(
                review,
                subject,
                revision,
                PublicationConfirmation(
                    assessment_id=assessment_id, expert_id=actor, quality_score=command.score, skills=experts
                ),
                now,
            )
        else:
            reviews = meta_data.tables["review_results"]
            await self.session.execute(
                update(reviews).where(reviews.c.id == review["id"]).values(state=ReviewState.NEEDS_REVISION)
            )
        await self.access.require_active(assignment)
        assignments, jobs = meta_data.tables["review_assignments"], meta_data.tables["review_jobs"]
        await self.session.execute(
            update(assignments).where(assignments.c.id == assignment["id"]).values(state=AssignmentState.COMPLETED)
        )
        await self.session.execute(
            update(jobs).where(jobs.c.id == assignment["job_id"]).values(state=JobState.COMPLETED)
        )
        return await self._finish(assessment_receipt(value(review, "id", UUID), record, 201), assignment)

    async def _publish(
        self,
        review: dict[str, object],
        subject: dict[str, object],
        revision: dict[str, object],
        confirmation: PublicationConfirmation,
        now: datetime,
    ) -> None:
        if subject["effective_review_id"] is not None:
            raise CaseConflictError("RESULT_CONFLICT", "Submission already has an effective review")
        original = meta_data.tables["review_assignments"]
        supervised = await self.session.scalar(
            select(original.c.supervised).where(original.c.id == review["assignment_id"])
        )
        if not supervised:
            raise CaseConflictError("SUPERVISION_REQUIRED", "Quality publication requires supervised provenance")
        snapshots, requirements = meta_data.tables["reviewer_snapshots"], meta_data.tables["submission_competencies"]
        rows = (
            (
                await self.session.execute(
                    select(snapshots, requirements.c.weight)
                    .join(
                        requirements,
                        (requirements.c.submission_id == subject["id"])
                        & (requirements.c.competency_id == snapshots.c.competency_id),
                    )
                    .where(snapshots.c.revision_id == revision["id"])
                    .order_by(snapshots.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        skills = tuple(
            PublicationSkill(
                competency_id=value(row, "competency_id", UUID),
                weight=value(row, "weight", Decimal),
                reviewer_value=value(row, "value", Decimal),
                reviewer_grade=value(row, "grade", SkillGrade),
                grant_id=value(row, "grant_id", UUID),
                grant_version=value(row, "grant_version", int),
                competency_version=value(row, "user_competency_version", int),
                grant_state=value(row, "grant_state", SkillGrantState),
            )
            for row in rows
        )
        reviews, submissions, projects = (
            meta_data.tables[name] for name in ("review_results", "submissions", "projects")
        )
        await self.session.execute(
            update(reviews)
            .where(reviews.c.id == review["id"])
            .values(state=ReviewState.PUBLISHED, published_at=now, published_revision_id=revision["id"])
        )
        await self.session.execute(
            update(submissions).where(submissions.c.id == subject["id"]).values(effective_review_id=review["id"])
        )
        case_id = await self.session.scalar(select(projects.c.case_id).where(projects.c.id == subject["project_id"]))
        if not isinstance(case_id, UUID):
            raise CaseNotFoundError
        await self.effects.apply(
            ReviewPublication(
                review_id=value(review, "id", UUID),
                revision_id=value(revision, "id", UUID),
                submission_id=value(subject, "id", UUID),
                case_id=case_id,
                case_version=value(subject, "case_version", int),
                author_id=UserId(value(subject, "author_id", UUID)),
                reviewer_id=UserId(value(review, "reviewer_id", UUID)),
                content=final_content(revision),
                skills=skills,
                created_at=now,
                supervision_required=True,
                revision_number=value(revision, "number", int),
                confirmation=confirmation,
            )
        )
        await self.session.execute(
            update(projects).where(projects.c.id == subject["project_id"]).values(updated_at=now)
        )

    async def revise(
        self,
        actor: UserId,
        review_id: UUID,
        expected_revision_id: UUID,
        expected_draft_version: int,
        key: str,
    ) -> CachedResponse:
        assignment, review, subject = await self._lock(actor, review_id, None)
        user = await self.access.actor(actor, lock=True)
        rights = await self.access.matching.rights(user, lock=True)
        await self.access.authorize(user, assignment, rights)
        if assignment["state"] != AssignmentState.COMPLETED or not assignment["supervised"]:
            raise CaseConflictError("STALE_REVISION", "Only completed supervised reviews can be corrected")
        prior = await self._revision(review_id, expected_revision_id)
        replay = await self.requests.begin(
            actor,
            "POST",
            f"/api/v1/reviews/{review_id}/revisions",
            key,
            encode({"expected_revision_id": expected_revision_id, "expected_draft_version": expected_draft_version}),
        )
        if replay is not None:
            return replace(replay, status_code=200)
        revisions = meta_data.tables["review_revisions"]
        existing = (
            (
                await self.session.execute(
                    select(revisions).where(
                        revisions.c.review_id == review_id, revisions.c.number == value(prior, "number", int) + 1
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if existing["source_draft_version"] != expected_draft_version:
                raise CaseConflictError("STALE_VERSION", "A different saved draft already replaced this revision")
            return await self._finish(revision_receipt(row_mapping(existing), 200))
        await self._require_current(review, prior, correction=True)
        if expected_draft_version <= value(prior, "source_draft_version", int):
            raise CaseConflictError("STALE_VERSION", "Save a corrected draft before submitting a revision")
        now = await database_now(self.session)
        requirements = await self.access.matching.requirements(value(subject, "id", UUID))
        skills = tuple(
            publication_skill(row_mapping(row), rights[value(row, "competency_id", UUID)]) for row in requirements
        )
        revision_id, _ = await save_revision(
            self.session,
            assignment_id=value(assignment, "id", UUID),
            review_id=review_id,
            submission_id=value(subject, "id", UUID),
            draft_version=expected_draft_version,
            number=value(prior, "number", int) + 1,
            now=now,
            skills=skills,
        )
        reviews = meta_data.tables["review_results"]
        await self.session.execute(update(reviews).where(reviews.c.id == review_id).values(state=ReviewState.PENDING))
        await enqueue_quality(self.session, value(subject, "id", UUID), revision_id, now)
        return await self._finish(revision_receipt(await self._revision(review_id, revision_id), 201))

    async def _require_current(
        self, review: dict[str, object], revision: dict[str, object], *, correction: bool = False
    ) -> None:
        revisions = meta_data.tables["review_revisions"]
        latest = await self.session.scalar(
            select(revisions.c.id)
            .where(revisions.c.review_id == review["id"])
            .order_by(revisions.c.number.desc())
            .limit(1)
        )
        if latest != revision["id"] or review["state"] != (
            ReviewState.NEEDS_REVISION if correction else ReviewState.PENDING
        ):
            raise CaseConflictError("STALE_REVISION", "Review revision or state changed")

    async def _revision(self, review_id: UUID, revision_id: UUID) -> dict[str, object]:
        revisions = meta_data.tables["review_revisions"]
        row = (
            (
                await self.session.execute(
                    select(revisions).where(
                        revisions.c.id == revision_id,
                        revisions.c.review_id == review_id,
                        revisions.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CaseNotFoundError
        return row_mapping(row)

    async def _lock(
        self,
        actor: UserId,
        review_id: UUID,
        assignment_id: UUID | None,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        reviews, assignments, jobs, users = (
            meta_data.tables[name] for name in ("review_results", "review_assignments", "review_jobs", "users")
        )
        statement = select(reviews).where(reviews.c.id == review_id, reviews.c.deleted_at.is_(None))
        if assignment_id is None:
            statement = statement.where(reviews.c.reviewer_id == actor)
        review = (await self.session.execute(statement)).mappings().one_or_none()
        if review is None:
            raise CaseNotFoundError
        target = assignment_id or value(review, "assignment_id", UUID)
        query = select(assignments).where(
            assignments.c.id == target,
            assignments.c.reviewer_id == actor,
            assignments.c.submission_id == review["submission_id"],
            assignments.c.deleted_at.is_(None),
        )
        assignment = (await self.session.execute(query)).mappings().one_or_none()
        if assignment is None:
            raise CaseNotFoundError
        subject = await self.access.subject(row_mapping(assignment))
        # All beneficiaries and the expert serialize in one deterministic order.
        for owner in sorted(
            {actor, value(review, "reviewer_id", UUID), value(subject, "author_id", UUID)}, key=lambda item: item.int
        ):
            if (
                await self.session.scalar(
                    select(users.c.id)
                    .where(users.c.id == owner, users.c.deleted_at.is_(None))
                    .with_for_update(key_share=True)
                )
                is None
            ):
                raise CaseNotFoundError
        await self.session.execute(select(jobs.c.id).where(jobs.c.id == assignment["job_id"]).with_for_update())
        locked_assignment = (await self.session.execute(query.with_for_update())).mappings().one()
        locked_review = (await self.session.execute(statement.with_for_update())).mappings().one()
        return (
            row_mapping(locked_assignment),
            row_mapping(locked_review),
            await self.access.subject(row_mapping(locked_assignment)),
        )

    async def _finish(self, response: CachedResponse, assignment: dict[str, object] | None = None) -> CachedResponse:
        await self.requests.complete(response)
        if assignment is not None:
            await self.access.require_active(assignment)
        await self.session.commit()
        return response


def assessment_receipt(review_id: UUID, record: Mapping[str, object], status: int) -> CachedResponse:
    location = f"/api/v1/reviews/{review_id}/quality-assessments/{record['id']}"
    fields = {
        key: record[key] for key in ("id", "assignment_id", "revision_id", "assessor_id", "decision", "created_at")
    }
    fields.update(review_id=review_id, content_url=location)
    return CachedResponse(
        status_code=status,
        location=location,
        body=encode(fields)[:-1] + b',"score":' + number(record["quality_score"]) + b"}",
    )


def revision_receipt(record: Mapping[str, object], status: int) -> CachedResponse:
    location = f"/api/v1/reviews/{record['review_id']}/revisions/{record['id']}"
    fields = {key: record[key] for key in ("id", "review_id", "number", "created_at")}
    fields.update(draft_version=record["source_draft_version"], content_url=location)
    return CachedResponse(status_code=status, location=location, body=encode(fields))
