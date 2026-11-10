from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(route_class=DishkaRoute, tags=["Health"])


class HealthcheckResponse(BaseModel):
    ok: bool = Field(..., description="Service health status")

    model_config = {"json_schema_extra": {"example": {"ok": True}}}


@router.get(
    "/healthcheck",
    summary="Health check",
    description="Check if the service is running and healthy",
    responses={
        200: {"description": "Service is healthy", "model": HealthcheckResponse},
    },
)
async def healthcheck() -> HealthcheckResponse:
    return HealthcheckResponse(ok=True)
