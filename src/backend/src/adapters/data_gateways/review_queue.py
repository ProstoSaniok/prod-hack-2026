import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.grade_evidence import SqlGradeEvidence
from src.adapters.data_gateways.learning_views import encode, row_mapping
from src.adapters.data_gateways.priority_scheduling import financial_effects, lock_scheduler, select_primary
from src.adapters.data_gateways.quality_matching import QualityMatching
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.review_jobs import reconcile_submissions
from src.adapters.data_gateways.review_views import ReviewViews, draft_input
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.review_requests import ReviewRequests
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.development.policy_types import Grant
from src.application.operations.idempotency import CachedResponse
from src.application.project.snapshot_reading import PinnedSource
from src.application.review.entity import AssignmentState, JobState, ReviewKind
from src.application.review.workspace import DraftContent
from src.application.user.entity import UserId
from src.application.wallet.entity import ReservationId, ReservationState
from src.application.wallet.policy import CancelledAllocation
from src.application.wallet.service import WalletCollaborator


class DefaultReviewStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.access, self.views, self.requests = ReviewAccess(session), ReviewViews(session), ReviewRequests(session)

    async def _enabled(self, actor: UserId) -> bool:
        profiles = meta_data.tables["user_profiles"]
        return bool(
            await self.session.scalar(
                select(profiles.c.wants_to_review).where(profiles.c.user_id == actor, profiles.c.deleted_at.is_(None))
            )
        )

    async def mode(self, actor: UserId) -> bytes:
        await self.access.owner(actor)
        try:
            user = await self.access.actor(actor)
            can_review = bool(await self.access.matching.rights(user))
            can_assess_readiness = bool(await SqlGradeEvidence(self.session).expert_skills(user))
        except ForbiddenError:
            can_review = can_assess_readiness = False
        enabled = await self._enabled(actor)
        tasks = await self.assignments(actor, limit=1, offset=0)
        status = "disabled" if not enabled else "assigned" if tasks != b"[]" else "waiting"
        return (
            encode({
                "enabled": enabled,
                "status": status,
                "can_review": can_review,
                "can_assess_readiness": can_assess_readiness,
            })[:-1]
            + b',"tasks":'
            + tasks
            + b"}"
        )

    async def set_mode(self, actor: UserId, enabled: bool, key: str) -> CachedResponse:  # noqa: FBT001
        user = await self.access.actor(actor, lock=True)
        if enabled and not await self.access.matching.rights(user, lock=True):
            raise ForbiddenError
        replay = await self.requests.begin(actor, "PUT", "/api/v1/me/review-mode", key, encode({"enabled": enabled}))
        if replay is not None:
            for task in json.loads(replay.body)["tasks"]:
                await self.access.owned(actor, UUID(task["id"]))
            return replay
        profiles = meta_data.tables["user_profiles"]
        await self.session.execute(
            pg_insert(profiles)
            .values(
                id=uuid4(),
                created_at=await database_now(self.session),
                user_id=actor,
                display_name="Участник",
                wants_to_review=enabled,
                version=1,
            )
            .on_conflict_do_update(
                index_elements=[profiles.c.user_id],
                set_={"wants_to_review": enabled, "version": profiles.c.version + 1},
                where=profiles.c.deleted_at.is_(None),
            )
        )
        return await self._finish(CachedResponse(status_code=200, body=await self.mode(actor)))

    async def claim(self, actor: UserId, key: str) -> CachedResponse:
        user = await self.access.actor(actor, lock=True)
        rights = await self.access.matching.rights(user, lock=True)
        if not rights:
            raise ForbiddenError
        replay = await self.requests.begin(actor, "POST", "/api/v1/me/review-assignments", key, b"{}")
        if replay is not None:
            if replay.body:
                assignment = await self.access.assignment(
                    actor, UUID(json.loads(replay.body)["id"]), lock=True, active=False
                )
                await self.access.authorize(user, assignment, rights)
                await self.access.require_active(assignment)
            return replace(replay, status_code=200) if replay.status_code == 201 else replay
        if not await self._enabled(actor):
            raise CaseConflictError("REVIEW_MODE_DISABLED", "Enable review mode before claiming work")
        streak = await lock_scheduler(self.session)
        assignments = meta_data.tables["review_assignments"]
        if (
            await self.session.scalar(
                select(assignments.c.id).where(
                    assignments.c.reviewer_id == actor, assignments.c.state == AssignmentState.ACTIVE
                )
            )
            is not None
        ):
            raise CaseConflictError("CAPACITY_LIMIT", "An active review assignment already exists")
        await reconcile_submissions(self.session)
        job = await QualityMatching(self.session).candidate(user, rights)
        if job is None:
            job = await select_primary(self.session, user, rights, streak)
        jobs = meta_data.tables["review_jobs"]
        if job is None:
            waiting = await self.session.scalar(
                select(jobs.c.id)
                .where(
                    jobs.c.kind.in_([ReviewKind.REVIEW, ReviewKind.QUALITY_CHECK]),
                    jobs.c.state == JobState.WAITING,
                    jobs.c.deleted_at.is_(None),
                )
                .limit(1)
            )
            return await self._finish(
                CachedResponse(
                    status_code=204,
                    body=b"",
                    queue_state_header=("X-Queue-State", "empty" if waiting is None else "no_eligible"),
                )
            )
        now, assignment_id = await database_now(self.session), uuid4()
        assignment = {
            "id": assignment_id,
            "created_at": now,
            "job_id": job["id"],
            "submission_id": job["submission_id"],
            "kind": job["kind"],
            "reviewer_id": actor,
            "lease_expires_at": None,
            "supervised": False,
            "state": AssignmentState.ACTIVE,
        }
        subject = await self.access.authorize(user, assignment, rights)
        requirements = await self.access.matching.requirements(value(subject, "id", UUID))
        assignment["supervised"] = assignment["kind"] == ReviewKind.REVIEW and any(
            rights[value(row, "competency_id", UUID)].authority.grant == Grant.SUPERVISED for row in requirements
        )
        await self.session.execute(insert(assignments).values(**assignment))
        skill_rows = []
        for row in requirements:
            skill = rights[value(row, "competency_id", UUID)]
            skill_rows.append({
                "id": uuid4(),
                "created_at": now,
                "assignment_id": assignment_id,
                "competency_id": skill.competency.competency_id,
                "grant_id": skill.grant.id,
                "grant_version": skill.grant.version,
                "user_competency_version": skill.competency.version,
                "value": skill.competency.displayed_value,
                "grade": skill.competency.grade,
                "grant_state": skill.grant.state,
                "probation_id": skill.probation_id,
            })
        if skill_rows:
            await self.session.execute(insert(meta_data.tables["assignment_competencies"]), skill_rows)
        await self.session.execute(
            insert(meta_data.tables["review_drafts"]).values(
                id=uuid4(),
                created_at=now,
                updated_at=now,
                assignment_id=assignment_id,
                version=1,
                strengths="",
                problems="",
                next_steps="",
            )
        )
        await self.session.execute(update(jobs).where(jobs.c.id == job["id"]).values(state=JobState.CLAIMED))
        await financial_effects(self.session, assignment)
        return await self._finish(
            CachedResponse(
                status_code=201,
                body=await self.views.task(assignment),
                location=f"/api/v1/review-assignments/{assignment_id}",
            ),
            assignment,
        )

    async def cancel(self, actor: UserId, assignment_id: UUID, key: str) -> CachedResponse:
        # Ownership, not current review rights, permits releasing an unusable allocation.
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        users, jobs, assignments, holds, submissions = (
            meta_data.tables[name]
            for name in ("users", "review_jobs", "review_assignments", "wallet_reservations", "submissions")
        )
        owner = await self.session.scalar(
            select(submissions.c.author_id).where(submissions.c.id == assignment["submission_id"])
        )
        if not isinstance(owner, UUID):
            raise CaseNotFoundError
        # Match completion's deterministic beneficiary -> job -> assignment -> wallet ordering.
        for identifier in sorted({actor, owner}, key=lambda item: item.int):
            await self.session.execute(
                select(users.c.id).where(users.c.id == identifier).with_for_update(key_share=True)
            )
        await self.access.owner(actor)
        assignment = await self.access.assignment(actor, assignment_id, lock=True, active=False)
        replay = await self.requests.begin(actor, "DELETE", f"/api/v1/review-assignments/{assignment_id}", key, b"{}")
        if replay is not None:
            return replay
        if assignment["state"] == AssignmentState.CANCELLED:
            return await self._finish(CachedResponse(status_code=204, body=b""))
        await self.access.require_active(assignment)
        changed = await self.session.scalar(
            update(jobs)
            .where(jobs.c.id == assignment["job_id"], jobs.c.state == JobState.CLAIMED)
            .values(state=JobState.WAITING)
            .returning(jobs.c.id)
        )
        if changed is None:
            raise CaseConflictError("ASSIGNMENT_CLOSED", "Assignment job is no longer claimed")
        await self.session.execute(
            update(assignments).where(assignments.c.id == assignment_id).values(state=AssignmentState.CANCELLED)
        )
        if assignment["kind"] == ReviewKind.REVIEW:
            refund = (
                (
                    await self.session.execute(
                        select(holds).where(
                            holds.c.assignment_id == assignment_id, holds.c.state == ReservationState.SETTLED
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if refund is not None:
                beneficiary = UserId(value(refund, "owner_id", UUID))
                wallet = await WalletCollaborator.acquire(
                    SqlAlchemyWalletTransaction(self.session), owner_ids=(beneficiary,)
                )
                await wallet.refund(
                    beneficiary,
                    ReservationId(value(refund, "id", UUID)),
                    CancelledAllocation(job_id=value(assignment, "job_id", UUID), assignment_id=assignment_id),
                    now=await database_now(self.session),
                )
        return await self._finish(CachedResponse(status_code=204, body=b""))

    async def _finish(self, response: CachedResponse, assignment: dict[str, object] | None = None) -> CachedResponse:
        await self.requests.complete(response)
        if assignment is not None:
            if assignment["state"] == AssignmentState.ACTIVE:
                await self.access.require_active(assignment)
            else:
                await self.access.editable(
                    UserId(value(assignment, "reviewer_id", UUID)), value(assignment, "id", UUID), lock=True
                )
        await self.session.commit()
        return response

    async def assignments(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        user = await self.access.owner(actor)
        try:
            await self.access.actor(actor)
            rights = await self.access.matching.rights(user)
        except ForbiddenError:
            rights = {}
        assignments = meta_data.tables["review_assignments"]
        # There can be only one active assignment; avoid bigint offset overflow.
        if offset > 0:
            return b"[]"
        rows = (
            await self.session.execute(
                select(assignments)
                .where(
                    assignments.c.reviewer_id == actor,
                    assignments.c.state == AssignmentState.ACTIVE,
                    assignments.c.kind.in_([ReviewKind.REVIEW, ReviewKind.QUALITY_CHECK]),
                    assignments.c.deleted_at.is_(None),
                )
                .order_by(assignments.c.created_at.desc(), assignments.c.id)
                .limit(limit)
            )
        ).mappings()
        tasks = []
        for row in rows:
            can_continue = True
            try:
                await self.access.authorize(user, row_mapping(row), rights)
            except (ForbiddenError, CaseNotFoundError, CaseConflictError):
                can_continue = False
            tasks.append(await self.views.task(row_mapping(row), can_continue=can_continue))
        return b"[" + b",".join(tasks) + b"]"

    async def assignment(self, actor: UserId, assignment_id: UUID) -> bytes:
        await self.access.owner(actor)
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        try:
            await self.access.editable(actor, assignment_id)
            can_continue = True
        except (ForbiddenError, CaseNotFoundError, CaseConflictError):
            can_continue = False
        return await self.views.task(assignment, can_continue=can_continue)

    async def workspace(self, actor: UserId, assignment_id: UUID) -> bytes:
        assignment, subject = await self.access.editable(actor, assignment_id)
        return await self.views.workspace(assignment, subject)

    async def draft(self, actor: UserId, assignment_id: UUID) -> bytes:
        assignment, _ = await self.access.editable(actor, assignment_id)
        return await self.views.draft(assignment)

    async def _draft_request(
        self, actor: UserId, assignment_id: UUID, content: DraftContent, key: str
    ) -> tuple[dict[str, object], CachedResponse | None]:
        assignment, _ = await self.access.editable(actor, assignment_id, lock=True)
        if assignment["kind"] != ReviewKind.REVIEW:
            raise CaseConflictError("ASSIGNMENT_KIND_MISMATCH", "Quality assignments do not accept review drafts")
        replay = await self.requests.begin(
            actor, "PUT", f"/api/v1/review-assignments/{assignment_id}/draft", key, draft_input(content)
        )
        return assignment, replay

    async def replay_draft(
        self, actor: UserId, assignment_id: UUID, content: DraftContent, key: str
    ) -> CachedResponse | None:
        # A completed private response does not depend on Git availability. Abort
        # any speculative reservation and release all locks before external I/O.
        try:
            _, replay = await self._draft_request(actor, assignment_id, content, key)
            return replay
        finally:
            await self.session.rollback()

    async def save_draft(self, actor: UserId, assignment_id: UUID, content: DraftContent, key: str) -> CachedResponse:
        assignment, replay = await self._draft_request(actor, assignment_id, content, key)
        if replay is not None:
            return replay
        drafts, comments = meta_data.tables["review_drafts"], meta_data.tables["review_comments"]
        draft_id = await self.session.scalar(
            update(drafts)
            .where(drafts.c.assignment_id == assignment_id, drafts.c.version == content.expected_version)
            .values(
                version=drafts.c.version + 1,
                score=content.score,
                verdict=content.verdict,
                strengths=content.strengths,
                problems=content.problems,
                next_steps=content.next_steps,
                updated_at=await database_now(self.session),
            )
            .returning(drafts.c.id)
        )
        if draft_id is None:
            raise CaseConflictError("STALE_VERSION", "Draft version changed")
        await self.session.execute(delete(comments).where(comments.c.draft_id == draft_id))
        now = await database_now(self.session)
        if content.comments:
            await self.session.execute(
                insert(comments),
                [
                    {
                        "id": uuid4(),
                        "created_at": now + timedelta(microseconds=index),
                        "draft_id": draft_id,
                        "text": comment.body,
                        "path": comment.path,
                        "line": comment.line,
                        "end_line": comment.end_line,
                        "side": comment.side,
                    }
                    for index, comment in enumerate(content.comments)
                ],
            )
        return await self._finish(CachedResponse(status_code=200, body=await self.views.draft(assignment)), assignment)

    async def source(self, actor: UserId, assignment_id: UUID) -> PinnedSource:
        assignment = await self.access.assignment(actor, assignment_id, active=False)
        if assignment["state"] == AssignmentState.COMPLETED:
            _, subject = await self.access.editable(actor, assignment_id)
        else:
            _, subject = await self.access.owned(actor, assignment_id)
        workspaces, results, intents = (
            meta_data.tables[name] for name in ("git_workspaces", "submission_results", "submission_intents")
        )
        row = (
            (
                await self.session.execute(
                    select(workspaces.c.name, results.c.commit_sha, intents.c.base_commit_sha)
                    .join(results, results.c.repository_id == workspaces.c.repository_id)
                    .join(intents, intents.c.operation_id == results.c.operation_id)
                    .where(results.c.operation_id == subject["operation_id"])
                )
            )
            .mappings()
            .one()
        )
        source = PinnedSource(
            submission_id=value(subject, "id", UUID),
            name=value(row, "name", str),
            base_sha=value(row, "base_commit_sha", str),
            head_sha=value(row, "commit_sha", str),
        )
        await self.session.commit()  # No SQL locks or transaction held across Git I/O.
        return source
