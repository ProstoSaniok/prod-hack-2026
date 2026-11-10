from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from src.application.review.results import ReviewResultsQueries
from src.application.review.usecases import CompleteReview
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery
from src.web_api.http.responses import json_response, review_response
from src.web_api.http.routing import ReviewRoute


class ReviewCompletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: int = Field(ge=1, strict=True)


router = APIRouter(
    route_class=ReviewRoute,
    prefix="/api/v1",
    tags=["Результаты рецензирования"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.post(
    "/review-assignments/{assignment_id}/reviews",
    status_code=201,
    responses={200: {"description": "Existing review or replay"}},
)
async def complete(
    assignment_id: UUID, body: ReviewCompletionInput, key: Key, interactor: FromDishka[CompleteReview]
) -> Response:
    return review_response(await interactor.execute(assignment_id, body.expected_draft_version, key))


@router.get("/me/reviews")
async def reviews(
    results: FromDishka[ReviewResultsQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0
) -> Response:
    return json_response(await results.reviews(limit=limit, offset=offset))


@router.get("/reviews/{review_id}")
async def review(review_id: UUID, results: FromDishka[ReviewResultsQueries]) -> Response:
    return json_response(await results.review(review_id))


@router.get("/submissions/{submission_id}/feedback")
async def feedback(submission_id: UUID, results: FromDishka[ReviewResultsQueries]) -> Response:
    return json_response(await results.feedback(submission_id))


@router.get("/submissions/{submission_id}/progression")
async def progression(submission_id: UUID, results: FromDishka[ReviewResultsQueries]) -> Response:
    return json_response(await results.progression(submission_id))
