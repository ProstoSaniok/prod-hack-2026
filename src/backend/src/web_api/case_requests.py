import json
from uuid import UUID

from fastapi import Request, Response

from src.application.catalog.entity import CaseId
from src.application.catalog.management import CaseAccess, CaseConflictError
from src.application.common.unit_of_work import UnitOfWork
from src.application.operations.clock import Clock
from src.application.operations.idempotency import (
    ArrayShape,
    CachedResponse,
    ObjectShape,
    RequestIdempotency,
    RequestJournal,
    RequestRecord,
    RequestScope,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)
from src.web_api.http.responses import present

STRING = ScalarShape(kinds=frozenset({ScalarKind.STRING}))


NUMBER = ScalarShape(kinds=frozenset({ScalarKind.NUMBER}))


BODY_SHAPE = ObjectShape(
    fields=(
        *(
            ResponseField(name=name, shape=STRING, required=True)
            for name in (
                "id",
                "title",
                "description",
                "expected_result",
                "difficulty",
                "status",
                "created_at",
                "updated_at",
            )
        ),
        ResponseField(name="version", shape=ScalarShape(kinds=frozenset({ScalarKind.INTEGER})), required=True),
        ResponseField(name="frozen", shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN})), required=True),
        ResponseField(
            name="competencies",
            shape=ArrayShape(
                max_items=100,
                items=ObjectShape(
                    fields=(
                        ResponseField(name="competency_id", shape=STRING, required=True),
                        ResponseField(name="required_value", shape=NUMBER, required=True),
                        ResponseField(name="weight", shape=NUMBER, required=True),
                    )
                ),
            ),
            required=True,
        ),
    )
)


class CaseRequests:
    def __init__(self, journal: RequestJournal, clock: Clock, unit_of_work: UnitOfWork, cases: CaseAccess) -> None:
        self.journal, self.clock, self.unit_of_work, self.cases = journal, clock, unit_of_work, cases

    async def begin(
        self, request: Request, key: str, case_id: CaseId | None = None
    ) -> tuple[RequestIdempotency, RequestRecord | CachedResponse]:
        actor = await self.cases.authorize(case_id, include_deleted=request.method == "DELETE")
        path = request.url.path
        policy = ResponseCapturePolicy(
            method=request.method,
            path=path,
            allowed_statuses=frozenset(
                {200, 201} if request.method == "POST" else {204 if request.method == "DELETE" else 200}
            ),
            body_shape=None if request.method == "DELETE" else BODY_SHAPE,
            allow_empty_body=request.method == "DELETE",
            location_prefix="/api/v1/admin/cases",
            max_body_bytes=256 * 1024,
        )
        handler = RequestIdempotency(clock=self.clock, response_policies=(policy,))
        payload = (
            await request.body()
            if request.method != "DELETE"
            else json.dumps({"query": list(request.query_params.multi_items())}).encode()
        )
        scope = RequestScope(actor_id=actor, method=request.method, path=path, key=key)
        record = await handler.begin(self.journal, scope, payload)
        if isinstance(record, CachedResponse) and request.method == "POST":
            # A cached create cannot expose a case after deletion or lost ownership.
            await self.cases.authorize(CaseId(UUID(json.loads(record.body)["id"])))
        return handler, record

    async def finish(self, handler: RequestIdempotency, record: RequestRecord, response: CachedResponse) -> Response:
        if not await handler.complete(
            self.journal,
            record,
            status_code=response.status_code,
            body=response.body,
            headers={"Location": response.location} if response.location else None,
        ):
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Request expired before completion")
        await self.unit_of_work.commit()
        return present(response)
