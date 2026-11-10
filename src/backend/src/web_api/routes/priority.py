from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer

from src.application.review.priority import PriorityQueries
from src.application.review.usecases import CancelReviewPriority, PurchaseReviewPriority
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery
from src.web_api.http.responses import json_response, review_response
from src.web_api.http.routing import ReviewRoute

router = APIRouter(
    route_class=ReviewRoute,
    prefix="/api/v1",
    tags=["Приоритет проверки"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
)


@router.post("/submissions/{submission_id}/priority-requests", status_code=201)
async def purchase(submission_id: UUID, key: Key, interactor: FromDishka[PurchaseReviewPriority]) -> Response:
    return review_response(await interactor.execute(submission_id, key))


@router.delete("/priority-requests/{request_id}", status_code=204)
async def cancel(request_id: UUID, key: Key, interactor: FromDishka[CancelReviewPriority]) -> Response:
    return review_response(await interactor.execute(request_id, key))


@router.get("/priority-requests/{request_id}")
async def get(request_id: UUID, priority: FromDishka[PriorityQueries]) -> Response:
    return json_response(await priority.get(request_id))


@router.get("/me/priority-requests")
async def listing(priority: FromDishka[PriorityQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0) -> Response:
    return json_response(await priority.list(limit, offset))
