import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.repository_storage.git import GitStorage
from tests.support.learning import MemoryGit, edited_solution, submission_project
from tests.support.reviews import Scenario, draft_body


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


def final_draft(version: int = 1, *, score: int | float = 80, marker: str = "Original") -> dict[str, object]:
    return {
        **draft_body(version),
        "score": score,
        "verdict": "approved" if score >= 70 else "changes_requested",
        "feedback": {
            "strengths": f"{marker} strengths",
            "problems": f"{marker} problems",
            "next_steps": f"{marker} steps",
        },
        "comments": [{"body": f"{marker} comment", "path": None, "line": None, "side": None}],
    }


async def save(client: AsyncClient, root: str, headers: dict[str, str], body: dict[str, object]) -> None:
    response = await client.put(
        root + "/draft", headers={**headers, "Idempotency-Key": f"save-{body['expected_version']}"}, json=body
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == value(body, "expected_version", int) + 1


async def complete(
    client: AsyncClient, root: str, headers: dict[str, str], *, version: int = 2, key: str = "complete"
) -> Response:
    return await client.post(
        root + "/reviews", headers={**headers, "Idempotency-Key": key}, json={"expected_draft_version": version}
    )


async def effects(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    """Persisted business facts, including fields whose updates leave counts unchanged."""
    result = {}
    async with engine.connect() as connection:
        for name in (
            "review_results",
            "review_revisions",
            "reviewer_snapshots",
            "review_comments",
            "review_drafts",
            "review_assignments",
            "review_jobs",
            "submissions",
            "projects",
            "user_competencies",
            "progression_contributions",
            "skill_grants",
            "probations",
            "probation_successes",
            "wallets",
            "wallet_ledger",
            "outbox_messages",
        ):
            table = meta_data.tables[name]
            rows = (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
            result[name] = [row_mapping(row) for row in rows]
    return result


async def second_submission(client: AsyncClient, engine: AsyncEngine, scenario: Scenario) -> Scenario:
    project, author, author_id, skills = await submission_project(
        client, engine, author_headers=scenario.author, skill_ids=scenario.skills, email_prefix="second-"
    )
    head = await edited_solution(client, project, author)
    response = await client.post(
        project + "/submissions", headers={**author, "Idempotency-Key": "submit"}, json={"expected_commit_sha": head}
    )
    assert response.status_code == 201, response.text
    return Scenario(project, author, author_id, skills, response.headers["location"])
