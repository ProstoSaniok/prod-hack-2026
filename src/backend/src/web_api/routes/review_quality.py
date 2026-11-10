from decimal import Decimal
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.application.review.entity import QualityDecision
from src.application.review.quality import QualityInput, ReviewQualityQueries
from src.application.review.usecases import AssessReviewQuality, ReviseReview
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery
from src.web_api.http.responses import json_response, review_response
from src.web_api.http.routing import ReviewRoute
from src.web_api.http.validation import utf8_text


class QualityAssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID
    revision_id: UUID
    decision: QualityDecision
    score: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    feedback: str = Field(min_length=1, max_length=6000, strict=True)

    @field_validator("score", mode="before")
    @classmethod
    def numeric_score(cls, value: object) -> object:
        if type(value) not in {int, Decimal}:
            raise ValueError("Quality score must be a JSON number")
        return value

    @field_validator("feedback")
    @classmethod
    def meaningful_feedback(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Quality feedback cannot be blank")
        return utf8_text(value)

    def command(self) -> QualityInput:
        return QualityInput(
            assignment_id=self.assignment_id,
            revision_id=self.revision_id,
            decision=self.decision,
            score=self.score,
            feedback=self.feedback,
        )


class ReviewRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision_id: UUID
    expected_draft_version: int = Field(ge=1, strict=True)


router = APIRouter(
    route_class=ReviewRoute,
    prefix="/api/v1",
    tags=["Проверка качества рецензий"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.post("/reviews/{review_id}/quality-assessments", status_code=201, responses={200: {"description": "Replay"}})
async def assess(
    review_id: UUID, body: QualityAssessmentInput, key: Key, interactor: FromDishka[AssessReviewQuality]
) -> Response:
    return review_response(await interactor.execute(review_id, body.command(), key))


@router.post("/reviews/{review_id}/revisions", status_code=201, responses={200: {"description": "Replay"}})
async def revise(
    review_id: UUID, body: ReviewRevisionInput, key: Key, interactor: FromDishka[ReviseReview]
) -> Response:
    return review_response(
        await interactor.execute(review_id, body.expected_revision_id, body.expected_draft_version, key)
    )


@router.get("/review-assignments/{assignment_id}/source-review")
async def source(assignment_id: UUID, quality: FromDishka[ReviewQualityQueries]) -> Response:
    return json_response(await quality.source(assignment_id))


@router.get("/reviews/{review_id}/quality-assessments")
async def assessments(
    review_id: UUID, quality: FromDishka[ReviewQualityQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0
) -> Response:
    return json_response(await quality.assessments(review_id, limit=limit, offset=offset))


@router.get("/reviews/{review_id}/quality-assessments/{assessment_id}")
async def assessment(review_id: UUID, assessment_id: UUID, quality: FromDishka[ReviewQualityQueries]) -> Response:
    return json_response(await quality.assessment(review_id, assessment_id))


@router.get("/reviews/{review_id}/revisions/{revision_id}")
async def revision(review_id: UUID, revision_id: UUID, quality: FromDishka[ReviewQualityQueries]) -> Response:
    return json_response(await quality.revision(review_id, revision_id))


@router.get("/me/probation")
async def probation(
    quality: FromDishka[ReviewQualityQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0
) -> Response:
    return json_response(await quality.probation(limit=limit, offset=offset))
