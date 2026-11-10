import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import access_token_table, auth_identity_table, meta_data, user_table
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserRole
from tests.support.auth import PASSWORD, register


async def test_sign_up_sign_in_and_sign_out(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    headers = await register(auth_client)
    current = await auth_client.get("/auth/me", headers=headers)
    assert current.status_code == 200
    assert current.json()["role"] == "user"
    assert current.json()["capabilities"] == ["participant"]
    mode = await auth_client.get("/api/v1/me/review-mode", headers=headers)
    assert mode.status_code == 200
    assert mode.json()["can_review"] is mode.json()["can_assess_readiness"] is False
    async with auth_engine.connect() as connection:
        password_hash = await connection.scalar(select(auth_identity_table.c.secret_key))
    assert password_hash != PASSWORD
    assert isinstance(password_hash, str)
    assert password_hash.startswith("$argon2")

    sign_in = await auth_client.post(
        "/auth/sign_in/email",
        json={"email": "USER@example.com", "password": PASSWORD},
    )
    assert sign_in.status_code == 200
    second_headers = {"Authorization": f"bearer {sign_in.json()['access_token']}"}
    assert (await auth_client.get("/auth/me", headers=second_headers)).json() == current.json()
    assert (await auth_client.post("/auth/sign_out", headers=headers)).status_code == 204
    assert (await auth_client.get("/auth/me", headers=headers)).status_code == 401
    assert (await auth_client.get("/auth/me", headers=second_headers)).status_code == 200


async def test_duplicate_email_and_invalid_credentials(auth_client: AsyncClient) -> None:
    await register(auth_client)
    duplicate = await auth_client.post(
        "/auth/sign_up/email",
        json={"email": "USER@example.com", "password": PASSWORD},
    )
    assert duplicate.status_code == 409
    for email in ("user@example.com", "missing@example.com"):
        response = await auth_client.post("/auth/sign_in/email", json={"email": email, "password": "wrong"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


async def test_concurrent_registration_is_atomic(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    responses = await asyncio.gather(
        *(
            auth_client.post("/auth/sign_up/email", json={"email": "user@example.com", "password": PASSWORD})
            for _ in range(2)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(user_table)) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"])) == 1
        assert await connection.scalar(select(meta_data.tables["wallets"].c.settled)) == 10
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["user_capabilities"])) == 1


async def test_legacy_login_provisions_participant_once_and_failed_login_does_not(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    headers = await register(auth_client)
    capabilities = meta_data.tables["user_capabilities"]
    async with auth_engine.begin() as connection:
        # A legacy user predates product capability records entirely.
        await connection.execute(delete(capabilities))
    failed = await auth_client.post("/auth/sign_in/email", json={"email": "user@example.com", "password": "wrong"})
    assert failed.status_code == 401
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(capabilities)) == 0
    results = await asyncio.gather(
        *(
            auth_client.post(
                "/auth/sign_in/email",
                json={
                    "email": "user@example.com",
                    "password": PASSWORD,
                },
            )
            for _ in range(3)
        )
    )
    assert all(result.status_code == 200 for result in results)
    assert (await auth_client.get("/auth/me", headers=headers)).json()["capabilities"] == ["participant"]
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(capabilities)) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["skill_grants"])) == 0


@pytest.mark.parametrize("kind", ["revoked", "deleted", "organizer", "revoked_organizer", "admin"])
async def test_login_preserves_historical_capability_decisions(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
    kind: str,
) -> None:
    headers = await register(auth_client)
    capabilities = meta_data.tables["user_capabilities"]
    async with auth_engine.begin() as connection:
        if kind == "admin":
            await connection.execute(delete(capabilities))
            await connection.execute(update(user_table).values(role=UserRole.ADMIN))
        elif "organizer" in kind:
            await connection.execute(
                update(capabilities).values(
                    capability=ProductCapability.ORGANIZER,
                    revoked_at=datetime.now(UTC) if kind == "revoked_organizer" else None,
                )
            )
        elif kind == "deleted":
            await connection.execute(update(capabilities).values(deleted_at=datetime.now(UTC)))
        else:
            await connection.execute(update(capabilities).values(revoked_at=datetime.now(UTC)))
        before = (await connection.execute(select(capabilities))).mappings().all()
    response = await auth_client.post("/auth/sign_in/email", json={"email": "user@example.com", "password": PASSWORD})
    assert response.status_code == 200
    async with auth_engine.connect() as connection:
        assert (await connection.execute(select(capabilities))).mappings().all() == before
    assert "participant" not in (await auth_client.get("/auth/me", headers=headers)).json()["capabilities"]


@pytest.mark.parametrize("authorization", ["", "Basic abc", "Bearer", "Bearer invalid", "Bearer a b"])
async def test_invalid_authorization(auth_client: AsyncClient, authorization: str) -> None:
    response = await auth_client.get("/auth/me", headers={"Authorization": authorization})
    assert response.status_code == 401


async def test_expired_token(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    headers = await register(auth_client)
    async with auth_engine.begin() as connection:
        await connection.execute(update(access_token_table).values(expires_in=datetime.now(tz=UTC) - timedelta(days=1)))
    assert (await auth_client.get("/auth/me", headers=headers)).status_code == 401


async def test_deleted_user_cannot_authenticate(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    headers = await register(auth_client)
    async with auth_engine.begin() as connection:
        await connection.execute(update(user_table).values(deleted_at=datetime.now(tz=UTC)))
    assert (await auth_client.get("/auth/me", headers=headers)).status_code == 401
    sign_in = await auth_client.post("/auth/sign_in/email", json={"email": "user@example.com", "password": PASSWORD})
    assert sign_in.status_code == 401


async def test_roles_cannot_be_self_assigned(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    response = await auth_client.post(
        "/auth/sign_up/email",
        json={"email": "user@example.com", "password": PASSWORD, "role": "admin"},
    )
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    current = await auth_client.get("/auth/me", headers=headers)
    assert current.json()["role"] == "user"
    assert (await auth_client.get("/_tests/admin")).status_code == 401
    assert (await auth_client.get("/_tests/admin", headers=headers)).status_code == 403
    async with auth_engine.begin() as connection:
        await connection.execute(
            update(user_table).where(user_table.c.id == UUID(current.json()["id"])).values(role=UserRole.ADMIN),
        )
    assert (await auth_client.get("/_tests/admin", headers=headers)).status_code == 200
