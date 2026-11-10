from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID, uuid4

from sqlalchemy import Select, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import LearningViews, encode, row_mapping
from src.adapters.data_gateways.review_access import ReviewAccess
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.priority_requests import PriorityRequestJournal
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.operations.idempotency import CachedResponse
from src.application.review.entity import JobState, ReviewKind
from src.application.review.priority import PRIORITY_POLICY_VERSION
from src.application.user.entity import UserId
from src.application.wallet.entity import ReservationId, ReservationState
from src.application.wallet.errors import InsufficientFundsError
from src.application.wallet.policy import PRIORITY_COST, PriorityJob
from src.application.wallet.service import WalletCollaborator


def records(actor: UserId) -> Select[tuple[object, ...]]:
    requests, holds, jobs, submissions, projects = (
        meta_data.tables[name]
        for name in ("priority_requests", "wallet_reservations", "review_jobs", "submissions", "projects")
    )
    return (
        select(requests, holds.c.state, holds.c.assignment_id, holds.c.settlement_entry_id, holds.c.terminal_entry_id)
        .join(holds, holds.c.id == requests.c.reservation_id)
        .join(jobs, jobs.c.id == requests.c.job_id)
        .join(submissions, submissions.c.id == requests.c.submission_id)
        .join(projects, projects.c.id == submissions.c.project_id)
        .where(
            requests.c.owner_id == actor,
            holds.c.owner_id == actor,
            submissions.c.author_id == actor,
            projects.c.participant_id == actor,
            submissions.c.deleted_at.is_(None),
            projects.c.deleted_at.is_(None),
            jobs.c.submission_id == submissions.c.id,
            jobs.c.kind == ReviewKind.REVIEW,
        )
    )


def response_body(row: Mapping[str, object]) -> bytes:
    status = {
        ReservationState.HELD: "reserved",
        ReservationState.SETTLED: "settled",
        ReservationState.RELEASED: "cancelled",
        ReservationState.REFUNDED: "refunded",
    }[value(row, "state", ReservationState)]
    return encode({
        **{
            name: row[name]
            for name in ("id", "submission_id", "job_id", "reservation_id", "owner_id", "cost", "created_at")
        },
        "status": status,
        "assignment_id": row["assignment_id"],
        "settlement_source_id": row["settlement_entry_id"],
        "refund_source_id": row["terminal_entry_id"] if row["state"] == ReservationState.REFUNDED else None,
    })


class DefaultPriorityStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session, self.access, self.requests = session, ReviewAccess(session), PriorityRequestJournal(session)

    async def purchase(self, actor: UserId, submission_id: UUID, key: str) -> CachedResponse:
        await self.access.actor(actor, lock=True)
        await LearningViews(self.session).submission_record(actor, submission_id)
        jobs, requests, holds = (
            meta_data.tables[name] for name in ("review_jobs", "priority_requests", "wallet_reservations")
        )
        job = (
            (
                await self.session.execute(
                    select(jobs)
                    .where(
                        jobs.c.submission_id == submission_id,
                        jobs.c.kind == ReviewKind.REVIEW,
                        jobs.c.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if job is None:
            raise CaseConflictError("JOB_UNAVAILABLE", "No primary review job exists")
        replay = await self.requests.begin(
            actor, "POST", f"/api/v1/submissions/{submission_id}/priority-requests", key, b"{}"
        )
        if replay is not None:
            return replace(replay, status_code=200)
        existing = (
            (
                await self.session.execute(
                    records(actor).where(requests.c.job_id == job["id"], holds.c.state == ReservationState.HELD)
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if existing["cost"] != PRIORITY_COST or existing["policy_version"] != PRIORITY_POLICY_VERSION:
                raise CaseConflictError(
                    "PRIORITY_ALREADY_ACTIVE", "Priority facts differ from the existing reservation"
                )
            return await self._finish(row_mapping(existing), 200)
        if job["state"] != JobState.WAITING:
            raise CaseConflictError("JOB_ALREADY_ASSIGNED", "Priority requires a waiting primary job")
        now, request_id, reservation_id = await database_now(self.session), uuid4(), ReservationId(uuid4())
        wallet = await WalletCollaborator.acquire(SqlAlchemyWalletTransaction(self.session), owner_ids=(actor,))
        try:
            await wallet.reserve(
                reservation_id, PriorityJob(job_id=job["id"], owner_id=actor, waiting_primary=True), now=now
            )
        except InsufficientFundsError:
            raise CaseConflictError("INSUFFICIENT_FUNDS", "Priority requires five available PROD credits") from None
        await self.session.execute(
            insert(requests).values(
                id=request_id,
                reservation_id=reservation_id,
                owner_id=actor,
                job_id=job["id"],
                submission_id=submission_id,
                created_at=now,
                cost=PRIORITY_COST,
                policy_version=PRIORITY_POLICY_VERSION,
            )
        )
        return await self._finish(await self._record(actor, request_id), 201)

    async def cancel(self, actor: UserId, request_id: UUID, key: str) -> CachedResponse:
        await self.access.actor(actor, lock=True)
        row = await self._record(actor, request_id)
        jobs = meta_data.tables["review_jobs"]
        job = (
            (await self.session.execute(select(jobs).where(jobs.c.id == row["job_id"]).with_for_update()))
            .mappings()
            .one()
        )
        row = await self._record(actor, request_id)
        replay = await self.requests.begin(actor, "DELETE", f"/api/v1/priority-requests/{request_id}", key, b"{}")
        if replay is not None:
            return replay
        if job["state"] != JobState.WAITING or row["state"] != ReservationState.HELD:
            raise CaseConflictError("PRIORITY_NOT_CANCELLABLE", "Only an unallocated reservation can be cancelled")
        wallet = await WalletCollaborator.acquire(SqlAlchemyWalletTransaction(self.session), owner_ids=(actor,))
        await wallet.release(
            actor, ReservationId(value(row, "reservation_id", UUID)), now=await database_now(self.session)
        )
        response = CachedResponse(status_code=204, body=b"")
        await self.requests.complete(response)
        await self.session.commit()
        return response

    async def _record(self, actor: UserId, request_id: UUID) -> dict[str, object]:
        table = meta_data.tables["priority_requests"]
        row = (await self.session.execute(records(actor).where(table.c.id == request_id))).mappings().one_or_none()
        if row is None:
            raise CaseNotFoundError
        return row_mapping(row)

    async def _finish(self, row: dict[str, object], status: int) -> CachedResponse:
        response = CachedResponse(
            status_code=status, body=response_body(row), location=f"/api/v1/priority-requests/{row['id']}"
        )
        await self.requests.complete(response)
        await self.session.commit()
        return response

    async def get(self, actor: UserId, request_id: UUID) -> bytes:
        await self.access.actor(actor)
        return response_body(await self._record(actor, request_id))

    async def list(self, actor: UserId, limit: int, offset: int) -> bytes:
        await self.access.actor(actor)
        if offset > 2**63 - 1:
            return b"[]"
        table = meta_data.tables["priority_requests"]
        rows = (
            await self.session.execute(
                records(actor).order_by(table.c.created_at.desc(), table.c.id).limit(limit).offset(offset)
            )
        ).mappings()
        return b"[" + b",".join(response_body(row_mapping(row)) for row in rows) + b"]"
