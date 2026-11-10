import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from src.application.user.entity import User
from src.web_api.identity_provider import WebApiIdentityProvider


async def test_catalogue_auth_storage_failure_is_safe(
    http_client: AsyncClient,
    backend: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(self: WebApiIdentityProvider) -> User:
        raise OperationalError("private SQL", {}, RuntimeError("private database details"))

    monkeypatch.setattr(WebApiIdentityProvider, "get_current_user", unavailable)
    response = await http_client.get("/api/v1/competencies")
    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "Catalogue temporarily unavailable",
        "request_id": response.headers["x-request-id"],
    }
    operation = backend.openapi()["paths"]["/api/v1/competencies"]["get"]
    assert operation["security"] == [{"OpaqueBearer": []}]
    for status in ("401", "422", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/CatalogErrorResponse",
        }
