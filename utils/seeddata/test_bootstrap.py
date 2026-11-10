"""Real signup/bootstrap regression checks against an isolated PostgreSQL schema."""

import os
from uuid import UUID, uuid4

import pytest

from utils.seeddata.server import router

from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from src.adapters.data_gateways.tables import meta_data
from src.application.common.containers import SecretString
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["performer", "organizer", "revoked", "deleted"])
async def test_bootstrap_preserves_signup_capability_and_history(monkeypatch, kind):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL database")
    schema = "seed_bootstrap_" + uuid4().hex
    engine = create_async_engine(
        url, execution_options={"schema_translate_map": {None: schema}}
    )
    monkeypatch.setenv("SEEDDATA_API_KEY", "test-bootstrap-key-" + "x" * 32)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    class EngineProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def provide_engine(self) -> AsyncEngine:
            return engine

    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(meta_data.create_all)
        config = Configuration(
            server=ServerConfiguration(),
            database=DatabaseConfiguration(url=SecretString("unused")),
            access_token=AccessTokenConfiguration(
                crypto_key=SecretString(Fernet.generate_key().decode())
            ),
        )
        app = make_asgi_application(make_ioc(config, EngineProvider()))
        app.include_router(router)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            signup = await client.post(
                "/auth/sign_up/email",
                json={
                    "email": "regression.seed@example.com",
                    "password": "seed-regression-password",
                },
            )
            assert signup.status_code == 200
            me = await client.get(
                "/auth/me",
                headers={"Authorization": "Bearer " + signup.json()["access_token"]},
            )
            user_id = UUID(me.json()["id"])
            assert me.json()["capabilities"] == ["participant"]
            capabilities = meta_data.tables["user_capabilities"]
            async with engine.begin() as connection:
                initial_id = await connection.scalar(
                    select(capabilities.c.id).where(capabilities.c.user_id == user_id)
                )
                if kind in {"revoked", "deleted"}:
                    column = (
                        capabilities.c.revoked_at
                        if kind == "revoked"
                        else capabilities.c.deleted_at
                    )
                    await connection.execute(
                        update(capabilities)
                        .where(capabilities.c.id == initial_id)
                        .values({column: func.now()})
                    )
            body = {
                "users": [
                    {
                        "id": str(user_id),
                        "email": "regression.seed@example.com",
                        "key": "regression",
                        "display_name": "Regression",
                        "kind": "organizer" if kind == "organizer" else "performer",
                        "initial_value": "0",
                    }
                ],
                "skills": [
                    {"slug": "sql", "name": "SQL", "description": "SQL regression"}
                ],
                "active_skill_slugs": ["sql"],
            }
            headers = {"X-Seeddata-Key": os.environ["SEEDDATA_API_KEY"]}
            for _ in range(2):
                response = await client.post(
                    "/seeddata/bootstrap", json=body, headers=headers
                )
                assert response.status_code == (
                    409 if kind in {"revoked", "deleted"} else 200
                )
            async with engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(capabilities).where(
                                capabilities.c.user_id == user_id
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(rows) == (2 if kind == "organizer" else 1)
                assert any(row["id"] == initial_id for row in rows)
                profiles = await connection.scalar(
                    select(func.count()).select_from(meta_data.tables["user_profiles"])
                )
                assert profiles == (0 if kind in {"revoked", "deleted"} else 1)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()
