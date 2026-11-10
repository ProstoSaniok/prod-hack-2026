from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_requests import SqlRequestJournal, SqlRequestOperationBindings
from src.adapters.operations.sql_work import database_now
from src.application.operations.idempotency import CachedResponse, PayloadMismatchError, RequestRecord, RequestScope
from src.application.project.capture import capture_policy, mutation_route
from src.application.project.learning import LearningCommand
from src.application.user.entity import UserId


class LearningRequests:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.journal = SqlRequestJournal(
            session,
            request_scopes_table=meta_data.tables["request_scopes"],
            request_generations_table=meta_data.tables["request_generations"],
        )
        self.bindings = SqlRequestOperationBindings(
            session,
            operations_table=meta_data.tables["operations"],
            request_scopes_table=meta_data.tables["request_scopes"],
            request_generations_table=meta_data.tables["request_generations"],
            request_operation_bindings_table=meta_data.tables["request_operation_bindings"],
        )
        self.record: RequestRecord | None = None

    async def reserve(self, operation_id: UUID, actor: UserId, command: LearningCommand, key: str, digest: str) -> None:
        method, path = mutation_route(command)
        now = await database_now(self.session)
        candidate = RequestRecord(
            id=uuid4(),
            scope=RequestScope(actor_id=actor, method=method, path=path, key=key),
            payload_hash=digest,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        record = await self.journal.reserve(candidate, now)
        if record.payload_hash != digest:
            raise PayloadMismatchError
        await self.bindings.bind(operation_id=operation_id, request_id=record.id)
        self.record = record

    async def complete(self, command: LearningCommand, response: CachedResponse) -> None:
        capture_policy(command).validate(response)
        if self.record is not None and self.record.response is None:
            now = await database_now(self.session)
            await self.journal.complete(self.record.id, response, now, now + timedelta(hours=24))

    async def lock(self, operation_id: UUID) -> None:
        if self.record is not None:
            await self.bindings.bind(operation_id=operation_id, request_id=self.record.id)
