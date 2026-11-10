from datetime import datetime
from typing import override

from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_requests import SqlRequestJournal
from src.adapters.operations.sql_work import database_now
from src.application.operations.idempotency import RequestRecord


class CaseRequestJournal(SqlRequestJournal):
    """Use PostgreSQL time for both sides of the reservation barrier, avoiding host clock skew."""

    def __init__(self, session: AsyncSession, timestamp: datetime) -> None:
        super().__init__(
            session,
            request_scopes_table=meta_data.tables["request_scopes"],
            request_generations_table=meta_data.tables["request_generations"],
        )
        self.session = session
        self.timestamp = timestamp

    def now(self) -> datetime:
        return self.timestamp

    @override
    async def reserve(self, candidate: RequestRecord, now: datetime) -> RequestRecord:
        result = await super().reserve(candidate, now)
        self.timestamp = await database_now(self.session)
        return result
