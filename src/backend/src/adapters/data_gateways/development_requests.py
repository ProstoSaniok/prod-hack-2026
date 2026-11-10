from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.operations.case_requests import CaseRequestJournal
from src.adapters.operations.sql_work import database_now
from src.application.catalog.management import CaseConflictError
from src.application.development.capture import capture_policy
from src.application.operations.idempotency import CachedResponse, RequestIdempotency, RequestRecord, RequestScope
from src.application.user.entity import UserId


class DevelopmentRequests:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.record: RequestRecord | None = None
        self.journal: CaseRequestJournal | None = None
        self.handler: RequestIdempotency | None = None

    async def begin(self, actor: UserId, path: str, key: str, body: bytes) -> CachedResponse | None:
        journal = CaseRequestJournal(self.session, await database_now(self.session))
        handler = RequestIdempotency(clock=journal, response_policies=(capture_policy(path),))
        record = await handler.begin(journal, RequestScope(actor_id=actor, method="POST", path=path, key=key), body)
        if isinstance(record, CachedResponse):
            return record
        self.record, self.journal, self.handler = record, journal, handler
        return None

    async def complete(self, response: CachedResponse) -> None:
        if self.record is None or self.journal is None or self.handler is None:
            raise ValueError("Development mutation has no request reservation")
        headers = (("Location", response.location),) if response.location is not None else ()
        if not await self.handler.complete(
            self.journal, self.record, status_code=response.status_code, body=response.body, headers=headers
        ):
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Development request capture expired")
