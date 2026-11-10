from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.repository_storage.git import GitStorage
from tests.support.learning import MemoryGit, edited_solution, participant, submission_project
from tests.support.review_completion import complete, effects, final_draft, save
from tests.support.reviews import MODE, Scenario, claim, seed_rights, submitted


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


async def quality_effects(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    """Include immutable quality evidence and original probation bindings in rollback checks."""
    result = await effects(engine)
    async with engine.connect() as connection:
        for name in ("assignment_competencies", "quality_assessments", "assessor_snapshots"):
            table = meta_data.tables[name]
            rows = (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
            result[name] = [row_mapping(row) for row in rows]
    return result


async def enable(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.put(MODE, headers={**headers, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert response.status_code == 200, response.text


async def expert(
    client: AsyncClient, engine: AsyncEngine, skills: list[UUID], email: str = "expert@example.com"
) -> tuple[dict[str, str], UUID]:
    headers = await participant(client, engine, email)
    user = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    await seed_rights(engine, user, skills, grade=SkillGrade.EXPERT)
    await enable(client, headers)
    return headers, user


async def provisional(client: AsyncClient, engine: AsyncEngine, skills: list[UUID]) -> tuple[dict[str, str], UUID]:
    headers = await participant(client, engine, "provisional@example.com")
    user = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    await seed_rights(engine, user, skills, state=SkillGrantState.PROVISIONAL)
    # Trusted import fixture: a grant alone is not coherent supervised authority.
    # Match each probation to the exact grant/user/skill/grade identity and epoch.
    competencies, grants, probations = (
        meta_data.tables[name] for name in ("user_competencies", "skill_grants", "probations")
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(competencies).where(competencies.c.user_id == user).values(contribution_sum=40, displayed_value=40)
        )
        rows = (await connection.execute(select(grants).where(grants.c.user_id == user))).mappings().all()
        await connection.execute(
            insert(probations),
            [
                {
                    "id": uuid4(),
                    "created_at": datetime.now(UTC),
                    "user_id": user,
                    "competency_id": row["competency_id"],
                    "grade": SkillGrade.REVIEW_MINUS,
                    "skill_grant_id": row["id"],
                    "epoch": 1,
                    "required_successes": 3,
                    "version": 1,
                }
                for row in rows
            ],
        )
    await enable(client, headers)
    return headers, user


async def another_submission(client: AsyncClient, engine: AsyncEngine, scenario: Scenario, prefix: str) -> Scenario:
    project, author, author_id, skills = await submission_project(
        client, engine, author_headers=scenario.author, skill_ids=scenario.skills, email_prefix=prefix
    )
    head = await edited_solution(client, project, author)
    response = await client.post(
        project + "/submissions", headers={**author, "Idempotency-Key": "submit"}, json={"expected_commit_sha": head}
    )
    assert response.status_code == 201, response.text
    return Scenario(project, author, author_id, skills, response.headers["location"])


async def pending_review(client: AsyncClient, headers: dict[str, str], key: str = "claim") -> tuple[Response, Response]:
    primary = await claim(client, headers, key)
    assert primary.status_code == 201, primary.text
    assert primary.json()["kind"] == "review"
    await save(client, primary.headers["location"], headers, final_draft())
    created = await complete(client, primary.headers["location"], headers)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending_quality_check"
    return primary, created


@dataclass(frozen=True)
class QualityScenario:
    submission: Scenario
    reviewer: dict[str, str]
    reviewer_id: UUID
    expert: dict[str, str]
    expert_id: UUID
    primary: Response
    created: Response


@pytest.fixture
async def quality_scenario(learning_client: AsyncClient, auth_engine: AsyncEngine) -> QualityScenario:
    scenario = await submitted(learning_client, auth_engine)
    headers, reviewer_id = await provisional(learning_client, auth_engine, scenario.skills)
    expert_headers, expert_id = await expert(learning_client, auth_engine, scenario.skills)
    primary, created = await pending_review(learning_client, headers)
    return QualityScenario(scenario, headers, reviewer_id, expert_headers, expert_id, primary, created)


async def assess(
    client: AsyncClient,
    review_url: str,
    assignment: Response,
    headers: dict[str, str],
    *,
    revision_id: str | None = None,
    decision: str = "confirmed",
    score: int = 80,
    key: str = "assess",
) -> Response:
    return await client.post(
        review_url + "/quality-assessments",
        headers={**headers, "Idempotency-Key": key},
        json={
            "assignment_id": assignment.json()["id"],
            "revision_id": revision_id or assignment.json()["revision_id"],
            "decision": decision,
            "score": score,
            "feedback": "Private expert feedback: explain the evidence and next steps.",
        },
    )


async def expire_request(engine: AsyncEngine, actor: UUID, path: str, key: str) -> None:
    now = datetime.now(UTC)
    generations, scopes = meta_data.tables["request_generations"], meta_data.tables["request_scopes"]
    async with engine.begin() as connection:
        request_id = (
            select(scopes.c.current_request_id)
            .where(scopes.c.actor_id == actor, scopes.c.path == path, scopes.c.request_key == key)
            .scalar_subquery()
        )
        await connection.execute(
            update(generations)
            .where(generations.c.id == request_id)
            .values(created_at=now - timedelta(days=3), expires_at=now - timedelta(days=1))
        )


async def revoke(engine: AsyncEngine, user: UUID) -> None:
    grants = meta_data.tables["skill_grants"]
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == user)
            .values(state=SkillGrantState.REVOKED, revoked_at=datetime.now(UTC), version=grants.c.version + 1)
        )
