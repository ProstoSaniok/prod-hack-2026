from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.application.common.containers import SecretString
from src.application.development.entity import SkillGrade, SkillGrantState
from src.web_api import entry_point, local_demo
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.ioc.make import make_ioc


async def test_demo_seed_twice_preserves_accounts_and_single_welcome_credit(auth_engine: AsyncEngine) -> None:
    class DemoDatabase(Provider):
        @provide(scope=Scope.APP, override=True)
        def engine(self) -> AsyncEngine:
            return auth_engine

    config = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
    )
    container = make_ioc(config, DemoDatabase())
    app = entry_point.make_asgi_application(container)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        await local_demo.seed_accounts(container, "Local-demo-2026!")
        async with auth_engine.connect() as connection:
            first_ids = set((await connection.execute(select(meta_data.tables["users"].c.id))).scalars())
        await local_demo.seed_accounts(container, "Local-demo-2026!")
        async with auth_engine.connect() as connection:
            assert set((await connection.execute(select(meta_data.tables["users"].c.id))).scalars()) == first_ids
            assert await connection.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"])) == len(
                first_ids
            )
        roles = {
            "performer": ("performer", 0),
            "quality-author": ("performer", 0),
            "quality-author-mobile": ("performer", 0),
            "quality-author-desktop": ("performer", 0),
            "review-minus": ("review_minus", 32),
            "review-plus": ("review_plus", 58),
            "mentor": ("expert", 80),
        }
        user_ids: dict[str, UUID] = {}
        for role in ("organizer", *roles):
            signed = await client.post(
                "/auth/sign_in/email", json={"email": f"{role}.local@example.com", "password": "Local-demo-2026!"}
            )
            assert signed.status_code == 200
            headers = {"Authorization": "Bearer " + signed.json()["access_token"]}
            user = (await client.get("/auth/me", headers=headers)).json()
            assert ("organizer" if role == "organizer" else "participant") in user["capabilities"]
            assert (await client.get("/api/v1/me/wallet", headers=headers)).json()["available"] == 10
            user_ids[role] = UUID(user["id"])
            if role != "organizer":
                profile = await client.get("/api/v1/me/profile", headers=headers)
                assert profile.status_code == 200
                skills = {skill["name"]: skill for skill in profile.json()["competencies"]}
                assert set(skills) == {"SQL", "Python", "Анализ данных"}
                grade, value = roles[role]
                for name, skill in skills.items():
                    assert skill["grade"] == grade
                    assert Decimal(str(skill["value"])) == (80 if name == "SQL" else value)
                    assert skill["grant"] == ("none" if grade == "performer" else "confirmed")
                    assert skill["review_allowed"] is (grade != "performer")
                    assert skill["expert_confirmed"] is (grade == "expert")

        skills_table = meta_data.tables["competencies"]
        current = meta_data.tables["user_competencies"]
        grants = meta_data.tables["skill_grants"]
        capabilities = meta_data.tables["user_capabilities"]
        now = datetime.now(UTC)
        async with auth_engine.begin() as connection:
            skill_ids = dict((await connection.execute(select(skills_table.c.name, skills_table.c.id))).tuples().all())
            # Simulate earned progress and deliberate removals before another startup seed.
            await connection.execute(
                update(current)
                .where(current.c.user_id == user_ids["performer"], current.c.competency_id == skill_ids["Python"])
                .values(
                    contribution_sum=Decimal("47.5"),
                    displayed_value=Decimal("47.5"),
                    grade=SkillGrade.REVIEW_MINUS,
                    version=4,
                )
            )
            await connection.execute(
                update(current)
                .where(current.c.user_id == user_ids["performer"], current.c.competency_id == skill_ids["SQL"])
                .values(contribution_sum=93, displayed_value=93, grade=SkillGrade.REVIEW_PLUS, version=5)
            )
            await connection.execute(
                update(current)
                .where(current.c.user_id == user_ids["review-plus"], current.c.competency_id == skill_ids["Python"])
                .values(deleted_at=now, version=2)
            )
            await connection.execute(
                update(grants)
                .where(grants.c.user_id == user_ids["review-minus"], grants.c.competency_id == skill_ids["Python"])
                .values(state=SkillGrantState.REVOKED, revoked_at=now, version=2)
            )
            await connection.execute(
                update(grants)
                .where(grants.c.user_id == user_ids["mentor"], grants.c.competency_id == skill_ids["Python"])
                .values(deleted_at=now, version=2)
            )
            await connection.execute(
                update(skills_table).where(skills_table.c.name == "Анализ данных").values(deleted_at=now)
            )
            await connection.execute(
                update(capabilities)
                .where(capabilities.c.user_id == user_ids["mentor"])
                .values(revoked_at=now, version=2)
            )
        preserved_tables = (
            "competencies",
            "user_competencies",
            "skill_grants",
            "user_capabilities",
            "wallets",
            "wallet_ledger",
        )
        before = {}
        async with auth_engine.connect() as connection:
            for name in preserved_tables:
                table = meta_data.tables[name]
                before[name] = (await connection.execute(select(table).order_by(*table.primary_key.columns))).all()
        await local_demo.seed_accounts(container, "Local-demo-2026!")
        async with auth_engine.connect() as connection:
            for name in preserved_tables:
                table = meta_data.tables[name]
                after = (await connection.execute(select(table).order_by(*table.primary_key.columns))).all()
                assert after == before[name], name


@pytest.mark.parametrize("enabled", [False, True])
async def test_cli_seeds_only_when_explicitly_enabled(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    seed = AsyncMock()
    server = Mock()
    server.serve = AsyncMock()
    monkeypatch.setattr(local_demo, "seed", seed)
    monkeypatch.setattr(entry_point, "load_configuration", Mock())
    monkeypatch.setattr(entry_point, "make_server", Mock(return_value=server))
    monkeypatch.delenv("LOCAL_DEMO_SEED", raising=False)
    if enabled:
        monkeypatch.setenv("LOCAL_DEMO_SEED", "true")
    path = Path("demo.toml")
    await entry_point.run_server(path)
    if enabled:
        seed.assert_awaited_once_with(path)
    else:
        seed.assert_not_awaited()
    server.serve.assert_awaited_once()
