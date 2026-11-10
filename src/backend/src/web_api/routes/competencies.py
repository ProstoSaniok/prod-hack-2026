from typing import Annotated
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from src.application.catalog.list_competencies import ListCompetencies
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.routing import CatalogRoute

router = APIRouter(route_class=CatalogRoute, prefix="/api/v1/competencies", tags=["Проекты"])


class CompetencyResponse(BaseModel):
    id: UUID
    name: str
    description: str


@router.get(
    "",
    operation_id="list_competencies",
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={
        401: {
            "model": CatalogErrorResponse,
            "description": "Missing, invalid, expired or revoked opaque bearer token",
            "headers": {
                "WWW-Authenticate": {"schema": {"type": "string", "enum": ["Bearer"]}},
                "X-Request-ID": {"schema": {"type": "string"}},
            },
        },
        422: {
            "model": CatalogErrorResponse,
            "description": "Invalid catalogue pagination",
            "headers": {"X-Request-ID": {"schema": {"type": "string"}}},
        },
        503: {
            "model": CatalogErrorResponse,
            "description": "Authentication or catalogue storage unavailable",
            "headers": {"X-Request-ID": {"schema": {"type": "string"}}},
        },
    },
)
async def list_competencies(
    interactor: FromDishka[ListCompetencies],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CompetencyResponse]:
    values = await interactor.execute(limit=limit, offset=offset)
    return [CompetencyResponse(id=value.id, name=value.name, description=value.description) for value in values]
