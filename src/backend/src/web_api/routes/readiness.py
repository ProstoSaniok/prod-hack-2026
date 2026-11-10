from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Response
from pydantic import BaseModel

from src.application.common.readiness import ReadinessProbe

router = APIRouter(route_class=DishkaRoute, tags=["Health"])


class DependencyChecks(BaseModel):
    database: bool
    repository_storage: bool


class ReadinessResponse(BaseModel):
    ok: bool
    checks: DependencyChecks


@router.get("/ready", responses={503: {"model": ReadinessResponse}}, summary="Dependency readiness")
async def readiness(probe: FromDishka[ReadinessProbe], response: Response) -> ReadinessResponse:
    result = await probe.check()
    response.status_code = 200 if result.ok else 503
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse(
        ok=result.ok,
        checks=DependencyChecks(database=result.database, repository_storage=result.repository_storage),
    )
