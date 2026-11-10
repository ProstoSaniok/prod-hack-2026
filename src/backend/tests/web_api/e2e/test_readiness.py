import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.readiness import Readiness
from src.adapters.gitea.transport import GiteaTransport
from src.adapters.operations.worker import ManagedWorker
from src.application.common.containers import SecretString
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc
from src.web_api.ioc.review_projection import make_review_projection_worker


@pytest.mark.parametrize("git_status", [200, 401])
async def test_readiness_production_probe_wiring(git_status: int) -> None:
    engine = MagicMock(spec=AsyncEngine)
    connection = engine.connect.return_value.__aenter__.return_value
    connection.scalar = AsyncMock(return_value=1)

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/healthz":
            return httpx.Response(200, json={"status": "pass"})
        assert request.headers["authorization"] == "token private-test-token"
        return httpx.Response(git_status, json={"id": 7, "login": "private-service-identity"})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))

    class ProbeProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def database_engine(self) -> AsyncEngine:
            return engine

        @provide(scope=Scope.APP, override=True)
        async def client(self) -> httpx.AsyncClient:
            return upstream_client

        @provide(scope=Scope.APP, override=True)
        def worker(self) -> ManagedWorker:
            # This probe fixture implements SELECT 1, not an outbox database.
            return make_review_projection_worker(engine, upstream_client, configuration.gitea, enabled=False)

    configuration = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
        gitea=GiteaConfiguration(
            internal_api_url=SecretString("https://gitea.invalid"), api_token=SecretString("private-test-token")
        ),
    )
    app = make_asgi_application(make_ioc(configuration, ProbeProvider()))
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client,
        ):
            response = await client.get("/ready")
            ready = git_status == 200
            assert response.status_code == (200 if ready else 503)
            assert response.json() == {"ok": ready, "checks": {"database": True, "repository_storage": ready}}
            assert response.headers["cache-control"] == "no-store"
            assert "private-" not in response.text
            assert (await client.get("/healthcheck")).json() == {"ok": True}
    finally:
        await upstream_client.aclose()


async def test_readiness_bounded_db_failure_and_unconfigured_git() -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.connect.return_value.__aenter__.side_effect = RuntimeError("private database credentials")
    async with httpx.AsyncClient() as client:
        probe = Readiness(engine, GiteaTransport(client, GiteaConfiguration()), timeout_seconds=0.05)
        result = await probe.check()
    assert not result.ok
    assert not result.database
    assert not result.repository_storage

    async def slow_connect() -> None:
        await asyncio.sleep(1)

    engine.connect.return_value.__aenter__.side_effect = slow_connect
    async with httpx.AsyncClient() as client:
        probe = Readiness(engine, GiteaTransport(client, GiteaConfiguration()), timeout_seconds=0.05)
        async with asyncio.timeout(0.5):
            assert not (await probe.check()).ok


async def test_production_app_client_closes_with_container() -> None:
    configuration = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("postgresql+psycopg://127.0.0.1:1/unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
    )
    ioc = make_ioc(configuration)
    app = make_asgi_application(ioc)
    async with app.router.lifespan_context(app):
        client = await ioc.get(httpx.AsyncClient)
        assert not client.is_closed
    assert client.is_closed


async def test_readiness_assigned_database_unconfigured_git(auth_client: httpx.AsyncClient) -> None:
    response = await auth_client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "checks": {"database": True, "repository_storage": False}}
    assert (await auth_client.get("/healthcheck")).json() == {"ok": True}
