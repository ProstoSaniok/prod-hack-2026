from typing import override

from src.adapters.operations.case_requests import CaseRequestJournal
from src.adapters.operations.review_requests import ReviewRequests
from src.adapters.operations.sql_work import database_now
from src.application.operations.idempotency import (
    CachedResponse,
    ObjectShape,
    RequestIdempotency,
    RequestScope,
    ResponseCapturePolicy,
)
from src.application.review.capture import fields
from src.application.user.entity import UserId


class PriorityRequestJournal(ReviewRequests):
    @override
    async def begin(self, actor: UserId, method: str, path: str, key: str, body: bytes) -> CachedResponse | None:
        policy = ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({204}) if method == "DELETE" else frozenset({200, 201}),
            allow_empty_body=method == "DELETE",
            max_body_bytes=65_536,
            location_prefix="/api/v1/priority-requests",
            body_shape=ObjectShape(
                fields=fields(
                    "id",
                    "submission_id",
                    "job_id",
                    "reservation_id",
                    "owner_id",
                    "cost",
                    "status",
                    "assignment_id",
                    "settlement_source_id",
                    "refund_source_id",
                    "created_at",
                )
            ),
        )
        journal = CaseRequestJournal(self.session, await database_now(self.session))
        handler = RequestIdempotency(clock=journal, response_policies=(policy,))
        record = await handler.begin(journal, RequestScope(actor_id=actor, method=method, path=path, key=key), body)
        if isinstance(record, CachedResponse):
            return record
        self.record, self.journal, self.handler = record, journal, handler
        return None
