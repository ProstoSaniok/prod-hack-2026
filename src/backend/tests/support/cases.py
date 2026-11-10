from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.application.user.capability import ProductCapability

PATH = "/api/v1/admin/cases"


async def account(
    client: AsyncClient, engine: AsyncEngine, email: str, *, organizer: bool = True
) -> tuple[UUID, dict[str, str]]:
    result = await client.post("/auth/sign_up/email", json={"email": email, "password": "case-test-password"})
    assert result.status_code == 200
    headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
    owner = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    if organizer:
        async with engine.begin() as connection:
            await connection.execute(
                insert(meta_data.tables["user_capabilities"]).values(
                    id=uuid4(),
                    created_at=datetime.now(UTC),
                    user_id=owner,
                    capability=ProductCapability.ORGANIZER,
                    version=1,
                )
            )
    return owner, headers


async def metadata(engine: AsyncEngine) -> dict[str, object]:
    skill = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert(meta_data.tables["competencies"]).values(
                id=skill,
                created_at=datetime.now(UTC),
                name="SQL",
                description="SQL proficiency",
            )
        )
    return {
        "title": "SQL case",
        "description": "Explain the query",
        "expected_result": "Correct joins",
        "competencies": [{"competency_id": str(skill), "required_value": 75, "weight": 0.25}],
    }
