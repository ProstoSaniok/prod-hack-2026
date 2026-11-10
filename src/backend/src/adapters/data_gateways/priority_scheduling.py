"""Fairness and all affected wallet effects in the allocating transaction."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import RowMapping, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.review_matching import ReviewMatching, ReviewerSkill
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.user.entity import User, UserId
from src.application.wallet.entity import ReservationId, ReservationState
from src.application.wallet.policy import Allocation
from src.application.wallet.service import WalletCollaborator


async def lock_scheduler(session: AsyncSession) -> int:
    table = meta_data.tables["priority_scheduler"]
    await session.execute(insert(table).values(name="primary", consecutive_priority=0).on_conflict_do_nothing())
    result = await session.scalar(
        select(table.c.consecutive_priority).where(table.c.name == "primary").with_for_update()
    )
    if not isinstance(result, int):
        raise TypeError("Missing scheduler state")
    return result


async def select_primary(
    session: AsyncSession, user: User, rights: dict[UUID, ReviewerSkill], streak: int
) -> RowMapping | None:
    matching = ReviewMatching(session)
    aged = await matching.candidate(user, rights, "aged")
    priority = None if aged is not None else await matching.candidate(user, rights, "priority")
    ordinary = aged if aged is not None else await matching.candidate(user, rights, "ordinary")
    chosen = ordinary if aged is not None or (ordinary is not None and (priority is None or streak >= 3)) else priority
    if chosen is not None:
        table = meta_data.tables["priority_scheduler"]
        await session.execute(
            update(table)
            .where(table.c.name == "primary")
            .values(consecutive_priority=min(3, streak + 1) if chosen is priority else 0)
        )
    return chosen


async def financial_effects(session: AsyncSession, assignment: Mapping[str, object]) -> None:
    """Settle a held primary priority after allocation; assignments never expire."""
    holds = meta_data.tables["wallet_reservations"]
    hold = (
        (
            await session.execute(
                select(holds).where(holds.c.job_id == assignment["job_id"], holds.c.state == ReservationState.HELD)
            )
        )
        .mappings()
        .one_or_none()
    )
    if hold is None:
        return
    owner = UserId(value(hold, "owner_id", UUID))
    wallets = await WalletCollaborator.acquire(SqlAlchemyWalletTransaction(session), owner_ids=(owner,))
    await wallets.settle(
        owner,
        ReservationId(value(hold, "id", UUID)),
        Allocation(
            job_id=value(assignment, "job_id", UUID), assignment_id=value(assignment, "id", UUID), eligible=True
        ),
        now=await database_now(session),
    )
