from decimal import Decimal
from typing import Annotated
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from src.application.development.entity import SkillGrade
from src.application.development.list_my_skills import ListMySkills
from src.web_api.http.routing import MySkillsRoute


class MySkillResponse(BaseModel):
    competency_id: UUID
    name: str
    grade: SkillGrade
    displayed_value: Decimal = Field(ge=0, le=100)


class MySkillsErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


router = APIRouter(
    route_class=MySkillsRoute,
    prefix="/api/v1/me/skills",
    tags=["Развитие"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={
        401: {
            "model": MySkillsErrorResponse,
            "description": "Missing, invalid, expired or revoked opaque bearer token",
            "headers": {"WWW-Authenticate": {"schema": {"type": "string", "enum": ["Bearer"]}}},
        },
        422: {"model": MySkillsErrorResponse, "description": "Invalid skills pagination"},
        503: {"model": MySkillsErrorResponse, "description": "Authentication or skills storage unavailable"},
    },
)


@router.get("", operation_id="list_my_skills")
async def list_my_skills(
    interactor: FromDishka[ListMySkills],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MySkillResponse]:
    skills = await interactor.execute(limit=limit, offset=offset)
    return [
        MySkillResponse(
            competency_id=skill.competency_id,
            name=skill.name,
            grade=skill.grade,
            displayed_value=skill.displayed_value,
        )
        for skill in skills
    ]
