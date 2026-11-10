"""PostgreSQL permanent journals, fenced claims and projection receipts.

Tables are the exact T02-owned central metadata exports supplied by the caller;
this module creates no metadata, sessions or commits. Use a fresh caller UoW for
each claim/finalization and restart the whole UoW on database contention errors.
The port's ``now`` remains accepted, but database wall time after locking decides
lease validity. Do not log SQL parameters or raw driver errors at the boundary.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Table, and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.operations.sql_records import (
    Row,
    operation_from_row,
    outbox_from_row,
    projection_from_row,
    work_values,
)
from src.application.operations.clock import require_aware
from src.application.operations.entity import (
    Operation,
    OutboxMessage,
    ProjectionSpec,
    SourceConflictError,
    validate_name,
)
from src.application.operations.recovery import validate_external_id
from src.application.operations.work import RetryPolicy, Work, WorkState


async def database_now(session: AsyncSession) -> datetime:
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    if not isinstance(now, datetime):
        raise TypeError("Database clock did not return a timestamp")
    require_aware(now)
    return now


def require_pending(work: Work) -> None:
    if work.state != WorkState.PENDING or work.attempts != 0 or work.lease is not None or work.last_error is not None:
        raise ValueError("New journal work must be pending and unattempted")


class _WorkRepository[Record: (Operation, OutboxMessage)]:
    def __init__(self, session: AsyncSession, table: Table, decode: Callable[[Row], Record]) -> None:
        self._session = session
        self._table = table
        self._decode: Callable[[Row], Record] = decode

    async def claim_next(self, *, now: datetime, owner: str, policy: RetryPolicy) -> Record | None:
        require_aware(now)
        table = self._table
        # Bound one transaction's quarantine work. The caller commits even when
        # None is returned; later invocations continue draining exhausted rows.
        for _ in range(100):
            cutoff = await database_now(self._session)
            due = or_(
                and_(table.c.state.in_((WorkState.PENDING, WorkState.RETRY_WAIT)), table.c.available_at <= cutoff),
                and_(table.c.state == WorkState.RUNNING, table.c.lease_until <= cutoff),
            )
            effective_due = case((table.c.state == WorkState.RUNNING, table.c.lease_until), else_=table.c.available_at)
            row = (
                (
                    await self._session.execute(
                        select(table)
                        .where(due)
                        .order_by(effective_due, table.c.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            current = self._decode(row)
            locked_now = await database_now(self._session)
            if not current.work.is_due(locked_now):
                return None
            claimed = current.work.claim(now=locked_now, owner=owner, token=uuid4(), policy=policy)
            saved = (
                (
                    await self._session.execute(
                        update(table).where(table.c.id == current.id).values(**work_values(claimed)).returning(table)
                    )
                )
                .mappings()
                .one()
            )
            if claimed.state == WorkState.RUNNING:
                return self._decode(saved)
        return None

    async def _save_leased(self, record: Record, *, token: UUID, now: datetime) -> bool:
        require_aware(now)
        table = self._table
        row = (
            (await self._session.execute(select(table).where(table.c.id == record.id).with_for_update()))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return False
        current = self._decode(row)
        locked_now = await database_now(self._session)
        lease = current.work.lease
        if lease is None or lease.token != token or lease.until <= locked_now:
            return False
        if current.spec != record.spec or current.work.created_at != record.work.created_at:
            raise SourceConflictError("Leased journal identity cannot be changed")
        if record.work.attempts != current.work.attempts or record.work.state not in {
            WorkState.SUCCEEDED,
            WorkState.RETRY_WAIT,
            WorkState.NEEDS_ATTENTION,
        }:
            raise ValueError("Invalid leased journal transition")
        values = work_values(record.work)
        if isinstance(record, Operation):
            if record.external_id is not None:
                validate_external_id(record.external_id)
            values["external_id"] = record.external_id
        updated = await self._session.execute(
            update(table)
            .where(
                table.c.id == record.id,
                table.c.state == WorkState.RUNNING,
                table.c.lease_token == token,
                table.c.lease_until > func.clock_timestamp(),
            )
            .values(**values)
            .returning(table.c.id)
        )
        return updated.scalar_one_or_none() is not None


class SqlOperationJournal(_WorkRepository[Operation]):
    def __init__(self, session: AsyncSession, operations_table: Table) -> None:
        super().__init__(session, operations_table, operation_from_row)

    async def save_leased(self, operation: Operation, *, token: UUID, now: datetime) -> bool:
        return await self._save_leased(operation, token=token, now=now)

    async def reserve(self, candidate: Operation) -> Operation:
        require_pending(candidate.work)
        if candidate.external_id is not None:
            raise ValueError("New operation cannot already have an external outcome")
        table = self._table
        spec = candidate.spec
        await self._session.execute(
            insert(table)
            .values(
                id=candidate.id,
                kind=spec.kind,
                source_key=spec.source_key,
                subject_id=spec.subject_id,
                payload_hash=spec.payload_hash,
                **work_values(candidate.work),
            )
            .on_conflict_do_nothing(index_elements=[table.c.kind, table.c.source_key])
        )
        row = (
            (
                await self._session.execute(
                    select(table)
                    .where(table.c.kind == spec.kind, table.c.source_key == spec.source_key)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        record = operation_from_row(row)
        if record.spec != spec:
            raise SourceConflictError("Permanent operation source has different inputs")
        return record


class SqlOutbox(_WorkRepository[OutboxMessage]):
    def __init__(self, session: AsyncSession, outbox_messages_table: Table) -> None:
        super().__init__(session, outbox_messages_table, outbox_from_row)

    async def save_leased(self, message: OutboxMessage, *, token: UUID, now: datetime) -> bool:
        return await self._save_leased(message, token=token, now=now)

    async def enqueue(self, candidate: OutboxMessage) -> OutboxMessage:
        require_pending(candidate.work)
        table = self._table
        spec = candidate.spec
        await self._session.execute(
            insert(table)
            .values(
                id=candidate.id,
                topic=spec.topic,
                source_key=spec.source_key,
                subject_id=spec.subject_id,
                revision=spec.revision,
                **work_values(candidate.work),
            )
            .on_conflict_do_nothing(index_elements=[table.c.topic, table.c.source_key])
        )
        row = (
            (
                await self._session.execute(
                    select(table)
                    .where(table.c.topic == spec.topic, table.c.source_key == spec.source_key)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        record = outbox_from_row(row)
        if record.spec != spec:
            raise SourceConflictError("Permanent outbox source has different inputs")
        return record


class SqlProjectionReceipts:
    def __init__(self, session: AsyncSession, projection_receipts_table: Table) -> None:
        self._session = session
        self._table = projection_receipts_table

    async def reserve(self, *, consumer: str, spec: ProjectionSpec) -> bool:
        validate_name(consumer)
        table = self._table
        inserted = await self._session.execute(
            insert(table)
            .values(
                consumer=consumer,
                topic=spec.topic,
                source_key=spec.source_key,
                subject_id=spec.subject_id,
                revision=spec.revision,
            )
            .on_conflict_do_nothing(index_elements=[table.c.consumer, table.c.topic, table.c.source_key])
            .returning(table.c.consumer)
        )
        if inserted.scalar_one_or_none() is not None:
            return True
        existing = (
            (
                await self._session.execute(
                    select(table)
                    .where(
                        table.c.consumer == consumer, table.c.topic == spec.topic, table.c.source_key == spec.source_key
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if projection_from_row(existing) != spec:
            raise SourceConflictError("Permanent projection receipt has different inputs")
        return False
