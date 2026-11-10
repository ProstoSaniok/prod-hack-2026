"""Owner submission priority availability, without allocation or financial writes."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.review.entity import JobState, ReviewKind
from src.application.user.entity import UserId
from src.application.wallet.entity import ReservationState
from src.application.wallet.policy import PRIORITY_COST


async def submission_priority(session: AsyncSession, actor: UserId, submission_id: UUID) -> dict[str, object]:
    jobs, requests, holds, wallets = (
        meta_data.tables[name] for name in ("review_jobs", "priority_requests", "wallet_reservations", "wallets")
    )
    latest = (
        select(requests)
        .where(requests.c.owner_id == actor, requests.c.submission_id == submission_id)
        .order_by(requests.c.created_at.desc(), requests.c.id.desc())
        .limit(1)
        .subquery()
    )
    row = (
        (
            await session.execute(
                select(
                    jobs.c.state.label("job_state"),
                    latest,
                    holds.c.state.label("reservation_state"),
                    holds.c.assignment_id,
                    holds.c.settlement_entry_id,
                    holds.c.terminal_entry_id,
                    (wallets.c.settled - wallets.c.reserved).label("available"),
                )
                .select_from(jobs)
                .outerjoin(latest, latest.c.job_id == jobs.c.id)
                .outerjoin(holds, (holds.c.id == latest.c.reservation_id) & (holds.c.owner_id == actor))
                .outerjoin(wallets, wallets.c.owner_id == actor)
                .where(
                    jobs.c.submission_id == submission_id, jobs.c.kind == ReviewKind.REVIEW, jobs.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    current: dict[str, object] | None = None
    if row is not None and row["id"] is not None and row["reservation_state"] is not None:
        state = row["reservation_state"]
        current = {
            name: row[name]
            for name in (
                "id",
                "submission_id",
                "job_id",
                "reservation_id",
                "owner_id",
                "cost",
                "created_at",
                "assignment_id",
            )
        }
        current.update(
            status={
                ReservationState.HELD: "reserved",
                ReservationState.SETTLED: "settled",
                ReservationState.RELEASED: "cancelled",
                ReservationState.REFUNDED: "refunded",
            }[state],
            settlement_source_id=row["settlement_entry_id"],
            refund_source_id=row["terminal_entry_id"] if state == ReservationState.REFUNDED else None,
        )
    waiting = row is not None and row["job_state"] == JobState.WAITING
    held = row is not None and row["reservation_state"] == ReservationState.HELD
    return {
        "cost": PRIORITY_COST,
        "can_purchase": waiting
        and not held
        and row is not None
        and row["available"] is not None
        and row["available"] >= PRIORITY_COST,
        "can_cancel": waiting and held,
        "current_request": current,
    }
