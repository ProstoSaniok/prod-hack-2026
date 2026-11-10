import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from src.adapters.data_gateways.tables import meta_data
from src.application.common.authorization import require_role
from src.application.common.containers import SecretString
from src.application.common.identity_provider import IdentityProvider
from src.application.user.entity import UserRole
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc


@pytest.fixture
async def auth_engine() -> AsyncIterator[AsyncEngine]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL database to run auth e2e tests")
    schema = f"test_{uuid4().hex}"
    engine = create_async_engine(url, execution_options={"schema_translate_map": {None: schema}})
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(meta_data.create_all)
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


@pytest.fixture
async def auth_client(auth_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    class TestConnectionProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def engine(self) -> AsyncEngine:
            return auth_engine

    configuration = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
    )
    app = make_asgi_application(make_ioc(configuration, TestConnectionProvider()))
    router = APIRouter(route_class=DishkaRoute)

    @router.get("/_tests/admin")
    async def admin_only(identity: FromDishka[IdentityProvider]) -> dict[str, bool]:
        require_role(await identity.get_current_user(), UserRole.ADMIN)
        return {"ok": True}

    app.include_router(router)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client
