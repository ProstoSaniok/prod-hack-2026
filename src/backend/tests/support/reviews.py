from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import ReadinessDecision, SkillGrade, SkillGrantEvidenceKind, SkillGrantState
from src.application.repository_storage.git import GitStorage
from tests.support.learning import MemoryGit, edited_solution, participant, submission_project

MODE = "/api/v1/me/review-mode"


QUEUE = "/api/v1/me/review-assignments"


@pytest.fixture
def learning_storage() -> GitStorage:
    return MemoryGit()


@dataclass(frozen=True)
class Scenario:
    project: str
    author: dict[str, str]
    author_id: UUID
    skills: list[UUID]
    submission: str


async def submitted(client: AsyncClient, engine: AsyncEngine, *, required_value: int = 10) -> Scenario:
    project, author, author_id, skills = await submission_project(client, engine, required_value=required_value)
    await edited_solution(client, project, author, "Solution\u2028detail\r\v\n")
    old = await client.get(project + "/files/content", headers=author, params={"path": "obsolete.txt"})
    deleted = await client.delete(
        project + "/files/content",
        headers={**author, "Idempotency-Key": "remove"},
        params={"path": "obsolete.txt", "sha": old.json()["sha"]},
    )
    assert deleted.status_code == 204, deleted.text
    head = (await client.get(project, headers=author)).json()["head_commit_sha"]
    response = await client.post(
        project + "/submissions", headers={**author, "Idempotency-Key": "submit"}, json={"expected_commit_sha": head}
    )
    assert response.status_code == 201, response.text
    return Scenario(project, author, author_id, skills, response.headers["location"])


async def seed_rights(
    engine: AsyncEngine,
    user: UUID,
    skills: list[UUID],
    *,
    state: SkillGrantState = SkillGrantState.CONFIRMED,
    grade: SkillGrade = SkillGrade.REVIEW_MINUS,
) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        for skill in skills:
            facts = {"created_at": now, "user_id": user, "competency_id": skill, "grade": grade, "version": 1}
            competencies = meta_data.tables["user_competencies"]
            version = await connection.scalar(
                pg_insert(competencies)
                .values(**facts, id=uuid4(), contribution_sum=90, displayed_value=90)
                .on_conflict_do_update(
                    index_elements=[competencies.c.user_id, competencies.c.competency_id],
                    set_={"grade": grade, "displayed_value": 90, "version": competencies.c.version + 1},
                )
                .returning(competencies.c.version)
            )
            await connection.execute(
                insert(meta_data.tables["skill_grants"]).values(
                    **facts,
                    id=uuid4(),
                    user_competency_version=version,
                    state=state,
                    evidence_kind=SkillGrantEvidenceKind.SEED,
                    evidence="PRIVATE EVIDENCE",
                    seed_reference="PRIVATE SEED",
                    confirmed_at=now if state == SkillGrantState.CONFIRMED else None,
                    readiness_decision=ReadinessDecision.CONFIRMED,
                    confirmed_by_grade=SkillGrade.EXPERT,
                )
            )


async def reviewer(client: AsyncClient, engine: AsyncEngine, skills: list[UUID], email: str) -> dict[str, str]:
    headers = await participant(client, engine, email)
    user = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    await seed_rights(engine, user, skills)
    response = await client.put(MODE, headers={**headers, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert response.status_code == 200, response.text
    return headers


async def claim(client: AsyncClient, headers: dict[str, str], key: str = "claim") -> Response:
    return await client.post(QUEUE, headers={**headers, "Idempotency-Key": key})


async def counts(engine: AsyncEngine) -> tuple[int | None, ...]:
    async with engine.connect() as connection:
        result = [
            await connection.scalar(select(func.count()).select_from(meta_data.tables[name]))
            for name in (
                "review_jobs",
                "review_assignments",
                "review_drafts",
                "review_comments",
                "request_generations",
            )
        ]
    return tuple(result)


async def expire(engine: AsyncEngine, assignment_id: str) -> None:
    now = datetime.now(UTC)
    assignments = meta_data.tables["review_assignments"]
    async with engine.begin() as connection:
        await connection.execute(
            update(assignments)
            .where(assignments.c.id == UUID(assignment_id))
            .values(created_at=now - timedelta(days=2), lease_expires_at=now - timedelta(days=1))
        )


def draft_body(version: int = 1) -> dict[str, object]:
    return {
        "expected_version": version,
        "score": None,
        "verdict": None,
        "feedback": {"strengths": "", "problems": "", "next_steps": ""},
        "comments": [],
    }
