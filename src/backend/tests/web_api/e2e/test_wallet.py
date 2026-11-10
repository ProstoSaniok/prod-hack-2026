from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.adapters.data_gateways.tables import meta_data, user_table
from src.adapters.password_utils import ArgonPasswordUtils
from src.application.auth_identity.entity import AuthIdentity
from src.application.auth_identity.errors import AuthUnavailableError
from src.application.auth_identity.interactor import GrantWelcomeCredit
from src.application.common.containers import SecretString
from src.application.user.entity import User, UserRole

PASSWORD = "test-wallet-password"  # noqa: S105


async def test_wallet_signup_atomicity_and_private_reads(
    auth_client: AsyncClient, auth_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def sign_up(email: str) -> dict[str, str]:
        response = await auth_client.post("/auth/sign_up/email", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    alice = await sign_up("alice@example.com")
    alice_id = (await auth_client.get("/auth/me", headers=alice)).json()["id"]
    balance = await auth_client.get("/api/v1/me/wallet", headers=alice)
    assert balance.status_code == 200
    assert balance.json() == {"settled": 10, "reserved": 0, "available": 10, "unit": "PRODиты"}
    assert balance.headers["cache-control"] == "no-store"
    history = (await auth_client.get("/api/v1/me/wallet/transactions", headers=alice)).json()
    assert len(history) == 1
    assert history[0]["kind"] == "welcome"
    assert history[0]["sequence"] == 1
    assert history[0]["settled_delta"] == 10
    assert history[0]["source_id"] == f"welcome:{alice_id}"
    assert history[0]["priority_request_id"] is None

    duplicate = await auth_client.post("/auth/sign_up/email", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert duplicate.status_code == 409
    login = await auth_client.post("/auth/sign_in/email", json={"email": "alice@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert (await auth_client.get("/api/v1/me/wallet/transactions", headers=alice)).json() == history

    bob = await sign_up("bob@example.com")
    bob_id = (await auth_client.get("/auth/me", headers=bob)).json()["id"]
    async with auth_engine.begin() as connection:
        await connection.execute(update(user_table).where(user_table.c.id == UUID(bob_id)).values(role=UserRole.ADMIN))
    bob_history = (await auth_client.get("/api/v1/me/wallet/transactions", headers=bob)).json()
    assert len(bob_history) == 1
    assert bob_history[0]["source_id"] == f"welcome:{bob_id}"
    assert bob_history[0]["id"] != history[0]["id"]
    assert (await auth_client.get("/api/v1/me/wallet/transactions?offset=1", headers=bob)).json() == []
    assert (await auth_client.get(f"/api/v1/me/wallet/transactions?offset={2**80}", headers=bob)).json() == []
    assert (await auth_client.get("/api/v1/me/wallet/transactions?limit=0", headers=bob)).status_code == 422
    unauthorized = await auth_client.get("/api/v1/me/wallet")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"

    async def counts() -> list[int | None]:
        async with auth_engine.connect() as connection:
            return [
                await connection.scalar(select(func.count()).select_from(meta_data.tables[name]))
                for name in (
                    "users",
                    "auth_identities",
                    "access_token",
                    "user_capabilities",
                    "wallets",
                    "wallet_ledger",
                )
            ]

    before = await counts()
    original_welcome = GrantWelcomeCredit.execute

    async def fail_after_credit(self: GrantWelcomeCredit, user: User) -> None:
        await original_welcome(self, user)
        raise AuthUnavailableError

    with monkeypatch.context() as patch:
        patch.setattr(GrantWelcomeCredit, "execute", fail_after_credit)
        failed = await auth_client.post(
            "/auth/sign_up/email", json={"email": "carol@example.com", "password": PASSWORD}
        )
        assert failed.status_code == 503
        assert failed.json() == {"detail": "Authentication service unavailable"}
    assert await counts() == before
    carol = await sign_up("carol@example.com")
    assert (await auth_client.get("/api/v1/me/wallet", headers=carol)).json()["available"] == 10
    assert len((await auth_client.get("/api/v1/me/wallet/transactions", headers=carol)).json()) == 1

    legacy = User.factory()
    password_hash = await ArgonPasswordUtils().hash(SecretString(PASSWORD))
    async with AsyncSession(auth_engine) as session:
        session.add(legacy)
        await session.flush()
        session.add(AuthIdentity.factory(legacy.id, "legacy@example.com", password_hash))
        await session.commit()
    before = await counts()
    with monkeypatch.context() as patch:
        patch.setattr(GrantWelcomeCredit, "execute", fail_after_credit)
        failed_login = await auth_client.post(
            "/auth/sign_in/email", json={"email": "legacy@example.com", "password": PASSWORD}
        )
        assert failed_login.status_code == 503
    assert await counts() == before
    login = await auth_client.post("/auth/sign_in/email", json={"email": "legacy@example.com", "password": PASSWORD})
    assert login.status_code == 200
    legacy_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await auth_client.get("/api/v1/me/wallet", headers=legacy_headers)).json()["available"] == 10
