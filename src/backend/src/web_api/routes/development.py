from typing import Literal
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from src.application.development.usecases import ApplyReviewerApplication, AssessReviewerApplication
from src.application.development.workflow import ApplicationInput, AssessmentInput, Decision, DevelopmentQueries
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery
from src.web_api.http.responses import json_response, present
from src.web_api.http.routing import DevelopmentRoute


class ApplicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competency_id: UUID
    requested_grade: Literal["expert"]
    expected_user_competency_version: int = Field(ge=1, strict=True)


class AssessmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_application_status: Literal["pending"]
    expected_application_version: int = Field(ge=1, strict=True)
    expected_user_competency_version: int = Field(ge=1, strict=True)
    decision: Decision
    feedback: str = Field(min_length=1, max_length=6000)


router = APIRouter(
    route_class=DevelopmentRoute,
    prefix="/api/v1",
    tags=["Развитие"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.post("/reviewer-applications", status_code=201)
async def apply(body: ApplicationBody, key: Key, interactor: FromDishka[ApplyReviewerApplication]) -> Response:
    return present(
        await interactor.execute(
            ApplicationInput(
                competency_id=body.competency_id,
                expected_user_competency_version=body.expected_user_competency_version,
            ),
            key,
        )
    )


@router.get("/me/reviewer-applications")
async def applications(
    development: FromDishka[DevelopmentQueries],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await development.applications(limit=limit, offset=offset))


@router.get("/reviewer-applications")
async def inbox(
    development: FromDishka[DevelopmentQueries],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await development.inbox(limit=limit, offset=offset))


@router.get("/reviewer-applications/{application_id}/evidence/reviews/{review_id}")
async def evidence_review(
    application_id: UUID, review_id: UUID, development: FromDishka[DevelopmentQueries]
) -> Response:
    return json_response(await development.evidence_review(application_id, review_id))


@router.get("/reviewer-applications/{application_id}")
async def application(application_id: UUID, development: FromDishka[DevelopmentQueries]) -> Response:
    return json_response(await development.application(application_id))


@router.post("/reviewer-applications/{application_id}/assessments", status_code=201)
async def assess(
    application_id: UUID,
    body: AssessmentBody,
    key: Key,
    interactor: FromDishka[AssessReviewerApplication],
) -> Response:
    return present(
        await interactor.execute(
            application_id,
            AssessmentInput(
                expected_application_version=body.expected_application_version,
                expected_user_competency_version=body.expected_user_competency_version,
                decision=body.decision,
                feedback=body.feedback,
            ),
            key,
        )
    )


@router.get("/reviewer-applications/{application_id}/assessments/{assessment_id}")
async def assessment(
    application_id: UUID,
    assessment_id: UUID,
    development: FromDishka[DevelopmentQueries],
) -> Response:
    return json_response(await development.assessment(application_id, assessment_id))
