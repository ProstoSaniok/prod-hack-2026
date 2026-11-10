from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.adapters.access_token.cryptographer import FernetAccessTokenCryptographer
from src.adapters.data_gateways.auth import DefaultAuthRepository
from src.adapters.data_gateways.tables import meta_data
from src.adapters.password_utils import ArgonPasswordUtils
from src.application.access_token.entity import AccessToken
from src.application.auth_identity.entity import AuthIdentity
from src.application.common.containers import SecretString
from src.application.user.entity import User
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc


def test_clean_upgrade_has_single_head_and_no_metadata_drift(core_engine: Engine) -> None:
    assert set(inspect(core_engine).get_table_names()) == {*meta_data.tables, "alembic_version"}
    with core_engine.connect() as connection:
        assert len(connection.execute(text("SELECT version_num FROM alembic_version")).all()) == 1
        differences = compare_metadata(MigrationContext.configure(connection), meta_data)
        assert differences == []


@asynccontextmanager
async def auth_client(engine: AsyncEngine, crypto_key: str) -> AsyncIterator[AsyncClient]:
    class MigratedConnectionProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def database_engine(self) -> AsyncEngine:
            return engine

    configuration = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(crypto_key)),
    )
    app = make_asgi_application(make_ioc(configuration, MigratedConnectionProvider()))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_upgrade_from_auth_baseline_preserves_signin_tokens_and_legacy_roles(preceding_engine: Engine) -> None:
    crypto_key = Fernet.generate_key().decode()
    payload = {"email": "migration@example.com", "password": "test-only-migration-password"}
    baseline = create_async_engine(preceding_engine.url)
    try:
        # Seed the auth-only release's real persisted identity/token. Current signup
        # requires the wallet migration, which intentionally does not exist yet.
        cryptographer = FernetAccessTokenCryptographer(AccessTokenConfiguration(crypto_key=SecretString(crypto_key)))
        legacy_user = User.factory()
        identity = AuthIdentity.factory(
            legacy_user.id, payload["email"], await ArgonPasswordUtils().hash(SecretString(payload["password"]))
        )
        legacy_token = AccessToken.factory(legacy_user.id, cryptographer.expires_in)
        async with AsyncSession(baseline, expire_on_commit=False) as session:
            await DefaultAuthRepository(session).register(legacy_user, identity, legacy_token)
            await session.commit()
        token = cryptographer.crypto(legacy_token.id)
        # The current /auth/me also reads capabilities, whose table is introduced
        # after this baseline. Verify the legacy identity/token against its own
        # schema, then exercise the current HTTP contract after the upgrade.
        async with AsyncSession(baseline) as session:
            repository = DefaultAuthRepository(session)
            stored_token = await repository.find_token(legacy_token.id)
            assert stored_token is not None
            assert not stored_token.expired_predicate()
            assert cryptographer.decrypto(token) == stored_token.id
            stored_user = await repository.find_user(stored_token.user_id)
            assert stored_user is not None
            assert stored_user.id == legacy_user.id
            assert stored_user.role == "user"
            user = {"id": str(stored_user.id), "role": "user", "capabilities": []}
    finally:
        await baseline.dispose()
    with preceding_engine.begin() as connection:
        connection.execute(text("UPDATE users SET role = 'ADMIN' WHERE id = :id"), {"id": user["id"]})
    command.upgrade(Config("alembic.ini"), "head")
    migrated = create_async_engine(preceding_engine.url)
    try:
        async with auth_client(migrated, crypto_key) as client:
            me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200
            assert me.json() == {**user, "role": "admin"}
            assert (
                await client.get("/api/v1/me/wallet", headers={"Authorization": f"Bearer {token}"})
            ).status_code == 404
            signin = await client.post("/auth/sign_in/email", json=payload)
            assert signin.status_code == 200
            assert signin.json()["token_type"] == "bearer"  # noqa: S105
            wallet = await client.get("/api/v1/me/wallet", headers={"Authorization": f"Bearer {token}"})
            assert wallet.json() == {"settled": 10, "reserved": 0, "available": 10, "unit": "PRODиты"}
            signout = await client.post("/auth/sign_out", headers={"Authorization": f"Bearer {token}"})
            assert signout.status_code == 204
            denied = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert denied.status_code == 401
            assert denied.headers["www-authenticate"] == "Bearer"
    finally:
        await migrated.dispose()
    command.downgrade(Config("alembic.ini"), "3a655675991b")
    with preceding_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert connection.scalar(text("SELECT count(*) FROM auth_identities")) == 1
        assert connection.scalar(text("SELECT count(*) FROM access_token")) == 2
    assert set(inspect(preceding_engine).get_table_names()) == {
        "users",
        "auth_identities",
        "access_token",
        "alembic_version",
    }
