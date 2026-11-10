from dataclasses import asdict
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, WithJsonSchema

from src.application.development.entity import SkillGrade
from src.application.development.policy_types import Grant
from src.application.profile.queries import (
    Activity,
    DevelopmentPolicy,
    ExpertApplicationEligibility,
    GrantEvidence,
    ProfileQueries,
    ProfileWallet,
    SkillProbation,
)
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserRole
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import LimitQuery, OffsetQuery
from src.web_api.http.responses import encode_exact_json
from src.web_api.http.routing import ProfileRoute


class NextGradeResponse(BaseModel):
    grade: SkillGrade
    xp_remaining: Annotated[Decimal, WithJsonSchema({"type": "number", "minimum": 0})]
    successful_cases: int
    previous_probation_passed: bool


class ProfileCompetencyResponse(BaseModel):
    competency_id: UUID
    name: str
    contribution_sum: Annotated[Decimal, WithJsonSchema({"type": "number", "minimum": 0})]
    value: Annotated[Decimal, WithJsonSchema({"type": "number", "minimum": 0, "maximum": 100})]
    grade: SkillGrade
    version: int
    grant: Grant
    review_allowed: bool
    expert_confirmed: bool
    evidence: GrantEvidence | None
    probation: SkillProbation | None
    expert_application: ExpertApplicationEligibility
    next_grade: NextGradeResponse | None


class ProfileResponse(BaseModel):
    id: UUID
    display_name: str
    role: UserRole
    capabilities: tuple[ProductCapability, ...]
    grade: SkillGrade
    competencies: tuple[ProfileCompetencyResponse, ...]
    probation: tuple[SkillProbation, ...]
    wants_to_review: bool
    wallet: ProfileWallet
    karma: int
    prodits: int


router = APIRouter(
    route_class=ProfileRoute,
    prefix="/api/v1",
    tags=["Профиль"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 422, 503)},
)


@router.get("/me/profile", response_model=ProfileResponse)
async def profile(queries: FromDishka[ProfileQueries]) -> Response:
    return Response(encode_exact_json(asdict(await queries.profile())), media_type="application/json")


@router.get("/me/history", response_model=list[Activity])
async def history(queries: FromDishka[ProfileQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0) -> Response:
    return Response(
        encode_exact_json([asdict(item) for item in await queries.history(limit=limit, offset=offset)]),
        media_type="application/json",
    )


@router.get("/development/policy", response_model=DevelopmentPolicy)
async def development_policy(queries: FromDishka[ProfileQueries]) -> Response:
    return Response(encode_exact_json(asdict(await queries.development_policy())), media_type="application/json")
