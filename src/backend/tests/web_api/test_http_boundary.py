import json
from decimal import Decimal

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from src.application.auth_identity.errors import AuthError
from src.application.catalog.management import CaseNotFoundError
from src.application.repository_storage.ports import StorageError
from src.web_api.http.requests import CaseRequest
from src.web_api.http.routing import CaseRoute, LearningRoute, ReviewRoute


@pytest.mark.parametrize("route_type", [CaseRoute, LearningRoute, ReviewRoute])
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AuthError(), 401, "UNAUTHENTICATED"),
        (CaseNotFoundError(), 404, "NOT_FOUND"),
        (StorageError(), 503, "DEPENDENCY_UNAVAILABLE"),
    ],
)
async def test_shared_boundary_preserves_error_headers_and_hides_details(
    route_type: type[CaseRoute] | type[LearningRoute] | type[ReviewRoute],
    error: Exception,
    status: int,
    code: str,
) -> None:
    app = FastAPI()
    router = APIRouter(route_class=route_type)

    @router.get("/resource")
    async def endpoint() -> None:
        raise error

    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/resource")
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.headers["cache-control"] == "no-store"
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


async def test_metadata_parser_preserves_exact_decimal() -> None:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b'{"weight":0.12345678901234567890123456789}', "more_body": False}

    request = CaseRequest({"type": "http", "method": "POST"}, receive)
    assert (await request.json()) == {"weight": Decimal("0.12345678901234567890123456789")}
    assert json.loads(await request.body())["weight"] != Decimal("0.12345678901234567890123456789")
