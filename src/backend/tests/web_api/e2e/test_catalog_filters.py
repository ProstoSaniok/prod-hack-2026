from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from tests.support.learning import learning_client, learning_storage, submission_project

__all__ = ["learning_client", "learning_storage"]


async def test_catalog_filters_use_any_selected_published_skill_before_pagination(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    sql, python = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert(meta_data.tables["competencies"]),
            [
                {"id": skill, "created_at": datetime.now(UTC), "name": name, "description": name}
                for skill, name in ((sql, "SQL"), (python, "Python"))
            ],
        )
    cases: list[str] = []
    headers: dict[str, str] = {}
    for index, (skills, level) in enumerate((([sql], 20), ([python], 50), ([sql, python], 75))):
        project, headers, _, _ = await submission_project(
            client, engine, skill_ids=skills, required_value=level, email_prefix=f"filter-{index}-"
        )
        cases.append(str((await client.get(project, headers=headers)).json()["case_id"]))
    # Mutable metadata cannot change what a published-snapshot search finds.
    async with engine.begin() as connection:
        table = meta_data.tables["cases"]
        await connection.execute(update(table).where(table.c.id == UUID(cases[0])).values(title="Unpublished title"))
    response = await client.get(
        "/api/v1/cases", headers=headers, params={"competency_ids": f"{sql},{python}", "limit": 1}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == cases[2:]
    pages: list[str] = []
    for offset in range(3):
        response = await client.get(
            "/api/v1/cases",
            headers=headers,
            params={"competency_ids": f"{sql},{python}", "limit": 1, "offset": offset},
        )
        assert response.status_code == 200, response.text
        pages.extend(item["id"] for item in response.json())
    assert pages == list(reversed(cases))
    selected = await client.get(
        "/api/v1/cases",
        headers=headers,
        params={"competency_ids": str(sql), "q": "  SUBMISSION  ", "difficulty": "review_minus", "limit": 1},
    )
    assert selected.status_code == 200, selected.text
    assert [item["id"] for item in selected.json()] == cases[:1]
    assert (await client.get("/api/v1/cases", headers=headers, params={"q": "Unpublished"})).json() == []
    assert (await client.get("/api/v1/cases", headers=headers, params={"q": "%"})).json() == []
    assert (await client.get("/api/v1/cases", headers=headers, params={"competency_ids": str(uuid4())})).json() == []
    for params in ({"competency_ids": "not-a-uuid"}, {"difficulty": "performer"}, {"q": "\0"}):
        assert (await client.get("/api/v1/cases", headers=headers, params=params)).status_code == 422
    assert (await client.get("/api/v1/cases?q=a&q=b", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/cases", params={"competency_ids": str(sql)})).status_code == 401
