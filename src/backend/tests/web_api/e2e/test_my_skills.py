from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.my_skills import DefaultMySkillsReader
from src.adapters.data_gateways.tables import meta_data, user_table
from src.application.development.entity import SkillGrade
from src.application.development.list_my_skills import MySkill, MySkillsUnavailableError
from src.application.user.entity import UserId, UserRole
from tests.support.auth import PASSWORD, register


async def test_new_profile_skills_are_empty_and_reads_have_no_effects(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    headers = await register(auth_client)
    async with auth_engine.connect() as connection:
        before = {
            name: await connection.scalar(select(func.count()).select_from(table))
            for name, table in meta_data.tables.items()
        }
    for query in ("", "?offset=20", f"?offset={2**80}"):
        response = await auth_client.get(f"/api/v1/me/skills{query}", headers=headers)
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["cache-control"] == "no-store"
    async with auth_engine.connect() as connection:
        after = {
            name: await connection.scalar(select(func.count()).select_from(table))
            for name, table in meta_data.tables.items()
        }
    assert after == before


async def test_profile_skills_are_owner_scoped_with_persisted_grades(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    alice = await register(auth_client)
    alice_id = UUID((await auth_client.get("/auth/me", headers=alice)).json()["id"])
    bob_response = await auth_client.post(
        "/auth/sign_up/email", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert bob_response.status_code == 200
    bob = {"Authorization": f"Bearer {bob_response.json()['access_token']}"}
    bob_id = UUID((await auth_client.get("/auth/me", headers=bob)).json()["id"])
    competency_ids = [UUID(int=value) for value in range(1, 6)]
    grades = [SkillGrade.PERFORMER, SkillGrade.REVIEW_MINUS, SkillGrade.REVIEW_PLUS, SkillGrade.EXPERT]
    now = datetime.now(tz=UTC)
    skills = meta_data.tables["user_competencies"]
    async with auth_engine.begin() as connection:
        await connection.execute(update(user_table).where(user_table.c.id == bob_id).values(role=UserRole.ADMIN))
        await connection.execute(
            insert(meta_data.tables["competencies"]),
            [
                {
                    "id": identity,
                    "created_at": now,
                    "name": f"Skill {index}",
                    "description": "Private profile test",
                    "deleted_at": now if index == 4 else None,
                }
                for index, identity in enumerate(competency_ids)
            ],
        )
        await connection.execute(
            insert(skills),
            [
                {
                    "id": uuid4(),
                    "user_id": alice_id,
                    "competency_id": identity,
                    "created_at": now,
                    "contribution_sum": Decimal("90.12"),
                    "displayed_value": Decimal("90.12"),
                    "grade": grades[index % len(grades)],
                    "deleted_at": None,
                }
                for index, identity in enumerate(competency_ids)
            ]
            + [
                {
                    "id": uuid4(),
                    "user_id": bob_id,
                    "competency_id": competency_ids[0],
                    "created_at": now,
                    "contribution_sum": Decimal("1.25"),
                    "displayed_value": Decimal("1.25"),
                    "grade": SkillGrade.PERFORMER,
                    "deleted_at": None,
                },
                {
                    "id": uuid4(),
                    "user_id": bob_id,
                    "competency_id": competency_ids[1],
                    "created_at": now,
                    "contribution_sum": Decimal(50),
                    "displayed_value": Decimal(50),
                    "grade": SkillGrade.REVIEW_MINUS,
                    "deleted_at": now,
                },
            ],
        )
        before = (await connection.execute(select(skills).order_by(skills.c.id))).all()
    expected = [
        {
            "competency_id": str(competency_ids[index]),
            "name": f"Skill {index}",
            "grade": grade.value,
            "displayed_value": "90.12",
        }
        for index, grade in enumerate(grades)
    ]
    response = await auth_client.get(f"/api/v1/me/skills?user_id={bob_id}&owner_id={bob_id}", headers=alice)
    assert response.status_code == 200
    assert response.json() == expected
    assert (await auth_client.get(f"/api/v1/me/skills?owner_id={alice_id}", headers=bob)).json() == [
        {"competency_id": str(competency_ids[0]), "name": "Skill 0", "grade": "performer", "displayed_value": "1.25"}
    ]
    async with auth_engine.connect() as connection:
        assert (await connection.execute(select(skills).order_by(skills.c.id))).all() == before
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["skill_grants"])) == 0
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["probations"])) == 0


async def test_profile_skills_have_stable_bounded_pagination(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    headers = await register(auth_client)
    owner_id = UUID((await auth_client.get("/auth/me", headers=headers)).json()["id"])
    now = datetime.now(tz=UTC)
    competency_ids = [UUID(int=value) for value in range(1, 23)]
    async with auth_engine.begin() as connection:
        await connection.execute(
            insert(meta_data.tables["competencies"]),
            [
                {"id": identity, "created_at": now, "name": f"Skill {identity.int}", "description": ""}
                for identity in competency_ids
            ],
        )
        await connection.execute(
            insert(meta_data.tables["user_competencies"]),
            [
                {
                    "id": uuid4(),
                    "user_id": owner_id,
                    "competency_id": identity,
                    "created_at": now if identity.int > 2 else now - timedelta(days=1),
                    "contribution_sum": Decimal(0),
                    "displayed_value": Decimal(0),
                    "grade": SkillGrade.PERFORMER,
                }
                for identity in reversed(competency_ids)
            ],
        )
    first = await auth_client.get("/api/v1/me/skills", headers=headers)
    assert first.status_code == 200
    assert [row["competency_id"] for row in first.json()] == [str(identity) for identity in competency_ids[2:]]
    second = await auth_client.get("/api/v1/me/skills?limit=2&offset=20", headers=headers)
    assert [row["competency_id"] for row in second.json()] == [str(identity) for identity in competency_ids[:2]]
    assert len((await auth_client.get("/api/v1/me/skills?limit=100", headers=headers)).json()) == 22
    assert (await auth_client.get("/api/v1/me/skills?offset=22", headers=headers)).json() == []
    assert (await auth_client.get(f"/api/v1/me/skills?offset={2**80}", headers=headers)).json() == []
    for query in ("limit=0", "limit=101", "offset=-1", "limit=invalid"):
        invalid = await auth_client.get(f"/api/v1/me/skills?{query}", headers=headers)
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VALIDATION_ERROR"
        assert invalid.json()["request_id"] == invalid.headers["x-request-id"]
        assert invalid.headers["cache-control"] == "no-store"


async def test_profile_skills_require_live_authentication(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        denied = await auth_client.get("/api/v1/me/skills", headers=headers)
        assert denied.status_code == 401
        assert denied.json()["code"] == "UNAUTHENTICATED"
        assert denied.headers["www-authenticate"] == "Bearer"
        assert denied.headers["cache-control"] == "no-store"
    headers = await register(auth_client)
    assert (await auth_client.post("/auth/sign_out", headers=headers)).status_code == 204
    assert (await auth_client.get("/api/v1/me/skills", headers=headers)).status_code == 401
    sign_in = await auth_client.post("/auth/sign_in/email", json={"email": "user@example.com", "password": PASSWORD})
    headers = {"Authorization": f"Bearer {sign_in.json()['access_token']}"}
    async with auth_engine.begin() as connection:
        await connection.execute(update(user_table).values(deleted_at=datetime.now(tz=UTC)))
    assert (await auth_client.get("/api/v1/me/skills", headers=headers)).status_code == 401


async def test_profile_skills_storage_errors_are_sanitized(
    auth_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(self: DefaultMySkillsReader, owner_id: UserId, *, limit: int, offset: int) -> list[MySkill]:
        raise MySkillsUnavailableError("private storage details")

    headers = await register(auth_client)
    monkeypatch.setattr(DefaultMySkillsReader, "list_skills", unavailable)
    response = await auth_client.get("/api/v1/me/skills", headers=headers)
    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "Skills temporarily unavailable",
        "request_id": response.headers["x-request-id"],
    }
    assert response.headers["cache-control"] == "no-store"
