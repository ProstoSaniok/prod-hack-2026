"""Generation-preserving PostgreSQL request journals and immutable bindings.

Supply the corresponding T02-owned central tables and a caller-owned session.
No method commits. Acquire all relevant operation locks BEFORE any request-scope
locks in the same transaction. Discovery of an earlier-rank lock requires a fresh
whole-UoW retry; these primitives are not a transaction/finalizer coordinator.
Authorization and route capture policies remain application/HTTP responsibilities.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Table, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.operations.sql_records import request_from_row, scope_from_row, value
from src.adapters.operations.sql_work import database_now
from src.application.operations.clock import require_aware
from src.application.operations.entity import SourceConflictError
from src.application.operations.idempotency import (
    CANONICAL_PAYLOAD_VERSION,
    MIN_RETENTION,
    CachedResponse,
    IdempotencyValidationError,
    RequestRecord,
)


class SqlRequestJournal:
    def __init__(self, session: AsyncSession, *, request_scopes_table: Table, request_generations_table: Table) -> None:
        self._session = session
        self._scopes = request_scopes_table
        self._generations = request_generations_table

    async def reserve(self, candidate: RequestRecord, now: datetime) -> RequestRecord:
        require_aware(now)
        if candidate.response is not None:
            raise IdempotencyValidationError("New request reservation must be pending")
        scopes, generations = self._scopes, self._generations
        scope = candidate.scope
        scope_values = {
            "actor_id": scope.actor_id,
            "method": scope.method,
            "path": scope.path,
            "request_key": scope.key,
        }
        await self._session.execute(
            insert(scopes)
            .values(id=uuid4(), **scope_values)
            .on_conflict_do_nothing(
                index_elements=[scopes.c.actor_id, scopes.c.method, scopes.c.path, scopes.c.request_key]
            )
        )
        scoped = (
            (
                await self._session.execute(
                    select(scopes)
                    .where(
                        scopes.c.actor_id == scope.actor_id,
                        scopes.c.method == scope.method,
                        scopes.c.path == scope.path,
                        scopes.c.request_key == scope.key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        scope_id = value(scoped, "id", UUID)
        current_id = scoped["current_request_id"]
        current = None
        if current_id is not None:
            current = (
                (
                    await self._session.execute(
                        select(generations)
                        .where(generations.c.id == current_id, generations.c.scope_id == scope_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
        locked_now = await database_now(self._session)
        if candidate.created_at > locked_now or candidate.expires_at <= locked_now:
            raise IdempotencyValidationError("Request candidate is outside the database clock window")
        if current is not None:
            record = request_from_row(current, scope)
            if record.expires_at > locked_now:
                return record
        await self._session.execute(
            insert(generations).values(
                id=candidate.id,
                scope_id=scope_id,
                payload_hash=candidate.payload_hash,
                canonical_payload_version=CANONICAL_PAYLOAD_VERSION,
                created_at=candidate.created_at,
                expires_at=candidate.expires_at,
            )
        )
        await self._session.execute(
            update(scopes).where(scopes.c.id == scope_id).values(current_request_id=candidate.id)
        )
        return candidate

    async def complete(self, record_id: UUID, response: CachedResponse, now: datetime, expires_at: datetime) -> bool:
        require_aware(now)
        require_aware(expires_at)
        scopes, generations = self._scopes, self._generations
        scope_id = (
            await self._session.execute(select(generations.c.scope_id).where(generations.c.id == record_id))
        ).scalar_one_or_none()
        if scope_id is None:
            return False
        scoped = (
            (await self._session.execute(select(scopes).where(scopes.c.id == scope_id).with_for_update()))
            .mappings()
            .one()
        )
        if scoped["current_request_id"] != record_id:
            return False
        row = (
            (
                await self._session.execute(
                    select(generations)
                    .where(generations.c.id == record_id, generations.c.scope_id == scope_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        locked_now = await database_now(self._session)
        record = request_from_row(row, scope_from_row(scoped))
        if record.response is not None or record.expires_at <= locked_now:
            return False
        if record.created_at > locked_now:
            raise IdempotencyValidationError("Request creation is after the database clock")
        try:
            expiry = max(record.expires_at, expires_at, locked_now + MIN_RETENTION)
        except OverflowError:
            raise IdempotencyValidationError("Request expiry is outside the database clock range") from None
        header = response.queue_state_header
        result = await self._session.execute(
            update(generations)
            .where(
                generations.c.id == record_id,
                generations.c.scope_id == scope_id,
                generations.c.response_status.is_(None),
                generations.c.expires_at > func.clock_timestamp(),
            )
            .values(
                response_status=response.status_code,
                response_body=response.body,
                response_location=response.location,
                response_queue_header_name=header[0] if header else None,
                response_queue_header_value=header[1] if header else None,
                expires_at=func.greatest(expiry, func.clock_timestamp() + MIN_RETENTION),
            )
            .returning(generations.c.id)
        )
        return result.scalar_one_or_none() is not None


class SqlRequestOperationBindings:
    """Immutable one-operation-per-generation association, not content intention.

    ``bind`` must be the first request-locking step in its caller UoW, after all
    earlier-ranked operation locks. An existing exact association remains readable
    after request expiry; neither binding nor generation is deleted on TTL expiry.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        operations_table: Table,
        request_scopes_table: Table,
        request_generations_table: Table,
        request_operation_bindings_table: Table,
    ) -> None:
        self._session = session
        self._operations = operations_table
        self._scopes = request_scopes_table
        self._generations = request_generations_table
        self._bindings = request_operation_bindings_table

    async def get_operation_id(self, request_id: UUID) -> UUID | None:
        result = (
            await self._session.execute(
                select(self._bindings.c.operation_id).where(self._bindings.c.request_id == request_id)
            )
        ).scalar_one_or_none()
        if result is not None and not isinstance(result, UUID):
            raise TypeError("Invalid persisted operation binding")
        return result

    async def bind(self, *, operation_id: UUID, request_id: UUID) -> bool:
        operations, scopes, generations, bindings = (
            self._operations,
            self._scopes,
            self._generations,
            self._bindings,
        )
        operation = (
            await self._session.execute(
                select(operations.c.id).where(operations.c.id == operation_id).with_for_update()
            )
        ).scalar_one_or_none()
        if operation is None:
            raise IdempotencyValidationError("Binding requires an existing permanent operation")
        scope_id = (
            await self._session.execute(select(generations.c.scope_id).where(generations.c.id == request_id))
        ).scalar_one_or_none()
        if scope_id is None:
            raise IdempotencyValidationError("Binding requires an existing request generation")
        scoped = (
            (await self._session.execute(select(scopes).where(scopes.c.id == scope_id).with_for_update()))
            .mappings()
            .one()
        )
        row = (
            (
                await self._session.execute(
                    select(generations)
                    .where(generations.c.id == request_id, generations.c.scope_id == scope_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        locked_now = await database_now(self._session)
        existing = await self.get_operation_id(request_id)
        if existing is not None:
            if existing != operation_id:
                raise SourceConflictError("Request generation is already bound to another operation")
            return False
        request = request_from_row(row, scope_from_row(scoped))
        if scoped["current_request_id"] != request_id or request.expires_at <= locked_now:
            raise IdempotencyValidationError("New binding requires the current live request generation")
        if request.response is not None or request.created_at > locked_now:
            raise IdempotencyValidationError("New binding requires a live pending request")
        inserted = await self._session.execute(
            insert(bindings)
            .from_select(
                ["request_id", "operation_id", "created_at"],
                select(generations.c.id, literal(operation_id), func.clock_timestamp()).where(
                    generations.c.id == request_id,
                    generations.c.response_status.is_(None),
                    generations.c.expires_at > func.clock_timestamp(),
                ),
            )
            .returning(bindings.c.request_id)
        )
        if inserted.scalar_one_or_none() is None:
            raise IdempotencyValidationError("Request generation expired before binding")
        return True
