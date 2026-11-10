from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from tests.support.auth import register


async def test_competency_catalogue_is_authenticated_and_paginated(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
) -> None:
    table = meta_data.tables["competencies"]
    now = datetime.now(tz=UTC)
    ids = [UUID(int=value) for value in range(1, 5)]
    async with auth_engine.begin() as connection:
        await connection.execute(
            insert(table),
            [
                {
                    "id": identity,
                    "created_at": now if index < 2 else now - timedelta(days=1),
                    "deleted_at": now if index == 3 else None,
                    "name": f"Skill {index}",
                    "description": "Shared skill",
                }
                for index, identity in enumerate(ids)
            ],
        )
    denied = await auth_client.get("/api/v1/competencies")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert denied.json()["code"] == "UNAUTHENTICATED"
    assert denied.json()["request_id"] == denied.headers["x-request-id"]
    headers = await register(auth_client)
    first = await auth_client.get("/api/v1/competencies?limit=1", headers=headers)
    assert first.status_code == 200
    assert first.json() == [{"id": str(ids[0]), "name": "Skill 0", "description": "Shared skill"}]
    remaining = await auth_client.get("/api/v1/competencies?limit=2&offset=1", headers=headers)
    assert [row["id"] for row in remaining.json()] == [str(ids[1]), str(ids[2])]
    assert (await auth_client.get("/api/v1/competencies?offset=3", headers=headers)).json() == []
    assert (await auth_client.get("/api/v1/competencies?offset=9223372036854775808", headers=headers)).json() == []
    invalid = await auth_client.get("/api/v1/competencies?limit=101", headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(table)) == 4
    await auth_client.post("/auth/sign_out", headers=headers)
    assert (await auth_client.get("/api/v1/competencies", headers=headers)).status_code == 401
