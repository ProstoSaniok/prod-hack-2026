import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.review_effects import SqlReviewPublicationEffects
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.repository_storage.git import GitStorage
from src.application.repository_storage.ports import StorageError
from src.application.review.finalization import ReviewPublication
from tests.support.learning import edited_solution, learning_client, participant, submission_project
from tests.support.review_completion import complete, effects, final_draft, learning_storage, save, second_submission
from tests.support.reviews import MODE, claim, expire, reviewer, seed_rights, submitted

__all__ = ["learning_client"]


async def test_approved_exact_draft_publishes_private_results_and_permanent_once_only_effects(
    learning_client: AsyncClient,
    learning_storage: GitStorage,
    auth_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "reviewer@example.com")
    stranger = await participant(client, engine, "stranger@example.com")
    reviewer_id = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    body = final_draft()
    body["comments"] = [{"body": "Range", "path": "task.txt", "line": 1, "end_line": 1, "side": "new"}]
    await save(client, root, headers, body)
    before_feedback = (await client.get(scenario.submission + "/feedback", headers=scenario.author)).json()
    assert before_feedback["reviews"] == []
    assert before_feedback["effective_review_id"] is before_feedback["progression"] is None
    with monkeypatch.context() as patch:
        patch.setattr(learning_storage, "files", AsyncMock(side_effect=StorageError))
        patch.setattr(learning_storage, "snapshot", AsyncMock(side_effect=StorageError))
        created = await complete(client, root, headers)
    assert created.status_code == 201, created.text
    receipt, location = created.json(), created.headers["location"]
    assert set(receipt) == {
        "id",
        "assignment_id",
        "submission_id",
        "reviewer_id",
        "status",
        "revision_id",
        "revision_number",
        "draft_version",
        "created_at",
        "published_at",
        "content_url",
    }
    assert receipt["content_url"] == location == f"/api/v1/reviews/{receipt['id']}"
    assert (receipt["draft_version"], receipt["revision_number"], receipt["status"]) == (2, 1, "published")
    assert created.headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/me/wallet", headers=scenario.author)).json()["settled"] == 13
    assert (await client.get("/api/v1/me/wallet", headers=headers)).json()["settled"] == 15
    full_response = await client.get(location, headers=scenario.author)
    assert full_response.status_code == 200, full_response.text
    full = full_response.json(parse_float=Decimal)
    revision = full["current_revision"]
    assert (revision["id"], revision["draft_version"], revision["score"]) == (receipt["revision_id"], 2, 80)
    assert revision["feedback"] == body["feedback"]
    assert revision["comments"] == body["comments"]
    assert len(revision["reviewer_skills"]) == 4
    assert all(skill["value"] == 90 and skill["grant"] == "confirmed" for skill in revision["reviewer_skills"])
    submission = (await client.get(scenario.submission, headers=scenario.author)).json()
    assert (submission["status"], submission["effective_review_id"]) == ("approved", receipt["id"])
    priority = submission.pop("priority")
    assert priority == {"cost": 5, "can_purchase": False, "can_cancel": False, "current_request": None}
    assert (await client.get(scenario.project, headers=scenario.author)).json()["status"] == "completed"
    feedback = (await client.get(scenario.submission + "/feedback", headers=scenario.author)).json(parse_float=Decimal)
    progression = (await client.get(scenario.submission + "/progression", headers=scenario.author)).json(
        parse_float=Decimal
    )
    assert feedback == {
        "submission": submission,
        "reviews": [full],
        "effective_review_id": receipt["id"],
        "progression": progression,
    }
    assert progression["policy_version"] == "p02-v1"
    assert len(progression["contributions"]) == 4
    assert {item["competency_id"] for item in progression["contributions"]} == set(map(str, scenario.skills))
    for item in progression["contributions"]:
        assert (item["review_id"], item["revision_id"], item["assessor_id"]) == (
            receipt["id"],
            receipt["revision_id"],
            str(reviewer_id),
        )
        assert (item["score"], item["weight"], item["expertise_value"]) == (80, Decimal("0.125"), 90)
        assert (
            item["contribution"] == item["contribution_sum_after"] == item["displayed_value_after"] == Decimal("11.8")
        )
        assert item["contribution_sum_before"] == item["displayed_value_before"] == 0
        assert item["assessor_grant_version"] == item["assessor_competency_version"] == 1
    page = (await client.get("/api/v1/me/reviews?limit=1", headers=headers)).json()
    assert page == {"items": [receipt], "limit": 1, "offset": 0, "next_offset": None}
    assert (await client.get("/api/v1/me/reviews?offset=1", headers=headers)).json()["items"] == []
    assert (await client.get("/api/v1/me/reviews", headers=stranger)).json()["items"] == []
    for path in (location, scenario.submission + "/feedback", scenario.submission + "/progression"):
        assert (await client.get(path, headers=stranger)).status_code == 404
    assert "PRIVATE EVIDENCE" not in full_response.text
    assert "PRIVATE SEED" not in full_response.text
    await expire(engine, assigned.json()["id"])
    before = await effects(engine)
    assert len(before["review_revisions"]) == len(before["review_results"]) == 1
    assert len(before["progression_contributions"]) == 4
    assert next(row for row in before["wallets"] if row["owner_id"] == reviewer_id)["karma"] == 1
    for key in ("complete", "new-key"):
        replay = await complete(client, root, headers, key=key)
        assert replay.status_code == 200, replay.text
        assert replay.content == created.content
        assert replay.headers["location"] == location
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        generations, scopes = meta_data.tables["request_generations"], meta_data.tables["request_scopes"]
        request_id = (
            select(scopes.c.current_request_id)
            .where(
                scopes.c.actor_id == reviewer_id, scopes.c.path == root + "/reviews", scopes.c.request_key == "complete"
            )
            .scalar_subquery()
        )
        await connection.execute(
            update(generations)
            .where(generations.c.id == request_id)
            .values(created_at=now - timedelta(days=3), expires_at=now - timedelta(days=1))
        )
    expired_replay = await complete(client, root, headers)
    assert expired_replay.status_code == 200, expired_replay.text
    assert expired_replay.content == created.content
    assert expired_replay.headers["location"] == location
    assert await effects(engine) == before


async def test_final_validation_fresh_authority_supervision_and_return_gate_changes_requested(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "reviewer@example.com")
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    baseline = await effects(engine)
    assignments = meta_data.tables["review_assignments"]
    async with engine.begin() as connection:
        await connection.execute(update(assignments).values(supervised=True))
    supervised_incomplete = await complete(client, root, headers, version=1)
    assert supervised_incomplete.status_code == 409
    assert supervised_incomplete.json()["code"] == "SUPERVISION_REQUIRED"
    async with engine.begin() as connection:
        await connection.execute(update(assignments).values(supervised=False))
    assert (await complete(client, root, headers, version=1)).status_code == 422
    assert await effects(engine) == baseline
    await save(
        client, root, headers, {**final_draft(), "feedback": {"strengths": " ", "problems": "", "next_steps": ""}}
    )
    assert (await complete(client, root, headers)).status_code == 422
    await save(client, root, headers, {**final_draft(2, score=69.999), "verdict": "approved"})
    assert (await complete(client, root, headers, version=3)).status_code == 422
    await save(client, root, headers, final_draft(3, score=69.999))
    stale = await complete(client, root, headers, version=3)
    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_VERSION"
    assert (await complete(client, root, scenario.author, version=4)).status_code == 404
    assignments, grants = meta_data.tables["review_assignments"], meta_data.tables["skill_grants"]
    reviewer_id = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    async with engine.begin() as connection:
        await connection.execute(update(assignments).values(supervised=True))
    baseline = await effects(engine)
    supervised = await complete(client, root, headers, version=4)
    assert supervised.status_code == 409
    assert supervised.json()["code"] == "SUPERVISION_REQUIRED"
    assert await effects(engine) == baseline
    async with engine.begin() as connection:
        await connection.execute(update(assignments).values(supervised=False))
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == reviewer_id)
            .values(state=SkillGrantState.REVOKED, revoked_at=datetime.now(UTC), version=2)
        )
    baseline = await effects(engine)
    assert (await complete(client, root, headers, version=4)).status_code == 403
    assert await effects(engine) == baseline
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == reviewer_id)
            .values(state=SkillGrantState.CONFIRMED, revoked_at=None, version=3)
        )
    await expire(engine, assigned.json()["id"])
    assert (await client.get(root + "/workspace", headers=headers)).status_code == 200
    assert (await client.delete(root, headers={**headers, "Idempotency-Key": "return"})).status_code == 204
    baseline = await effects(engine)
    cancelled = await complete(client, root, headers, version=4)
    assert cancelled.status_code == 409
    assert cancelled.json()["code"] == "ASSIGNMENT_CLOSED"
    assert await effects(engine) == baseline
    second = await reviewer(client, engine, scenario.skills, "replacement@example.com")
    reclaimed = await claim(client, second)
    assert reclaimed.status_code == 201, reclaimed.text
    root = reclaimed.headers["location"]
    await save(client, root, second, final_draft(score=69.999))
    created = await complete(client, root, second)
    assert created.status_code == 201, created.text
    result = (await client.get(created.headers["location"], headers=scenario.author)).json(parse_float=Decimal)
    assert result["current_revision"]["score"] == Decimal("69.999")
    assert result["current_revision"]["verdict"] == "changes_requested"
    assert (await client.get("/api/v1/me/wallet", headers=scenario.author)).json()["settled"] == 10
    assert (await client.get("/api/v1/me/wallet", headers=second)).json()["settled"] == 15
    facts = await effects(engine)
    assert facts["progression_contributions"] == []
    assert all(
        row["contribution_sum"] == 0 for row in facts["user_competencies"] if row["user_id"] == scenario.author_id
    )
    assert sorted(value(row, "karma", int) for row in facts["wallets"]) == [0, 0, 0, 1]


async def test_effect_failure_rolls_back_and_concurrent_completion_and_draft_save_consume_one_version(
    learning_client: AsyncClient, auth_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "reviewer@example.com")
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    await save(client, root, headers, final_draft())
    original = SqlReviewPublicationEffects.apply
    baseline = await effects(engine)

    async def fail_after_effects(store: SqlReviewPublicationEffects, publication: ReviewPublication) -> None:
        await original(store, publication)
        assert (
            await store.session.scalar(select(func.count()).select_from(meta_data.tables["wallet_ledger"]))
            == len(baseline["wallet_ledger"]) + 2
        )
        assert (
            await store.session.scalar(select(func.count()).select_from(meta_data.tables["outbox_messages"]))
            == len(baseline["outbox_messages"]) + 1
        )
        raise SQLAlchemyError("injected failure after real publication effects")

    with monkeypatch.context() as patch:
        patch.setattr(SqlReviewPublicationEffects, "apply", fail_after_effects)
        failed = await complete(client, root, headers)
    assert failed.status_code == 503, failed.text
    assert "injected failure" not in failed.text
    assert await effects(engine) == baseline
    responses = await asyncio.gather(*(complete(client, root, headers, key=key) for key in ("race-a", "race-b")))
    assert sorted(response.status_code for response in responses) == [200, 201], [
        response.text for response in responses
    ]
    assert responses[0].content == responses[1].content
    assert responses[0].headers["location"] == responses[1].headers["location"]
    facts = await effects(engine)
    assert len(facts["review_results"]) == len(facts["review_revisions"]) == 1
    assert len(facts["progression_contributions"]) == 4
    assert len(facts["wallet_ledger"]) == len(baseline["wallet_ledger"]) + 2
    assert len(facts["outbox_messages"]) == len(baseline["outbox_messages"]) + 1
    scenario = await second_submission(client, engine, scenario)
    assigned = await claim(client, headers, "second-claim")
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    old, new = final_draft(marker="Old"), final_draft(2, score=90, marker="New")
    await save(client, root, headers, old)
    replaced, completed = await asyncio.gather(
        client.put(root + "/draft", headers={**headers, "Idempotency-Key": "replace"}, json=new),
        complete(client, root, headers),
    )
    if completed.status_code == 409:
        assert completed.json()["code"] == "STALE_VERSION"
        assert replaced.status_code == 200, replaced.text
        expected, version = new, 3
        completed = await complete(client, root, headers, version=3, key="consume-new")
    else:
        assert replaced.status_code == 409, replaced.text
        expected, version = old, 2
    assert completed.status_code == 201, completed.text
    revision = (await client.get(completed.headers["location"], headers=scenario.author)).json()["current_revision"]
    assert (revision["draft_version"], revision["score"], revision["feedback"], revision["comments"]) == (
        version,
        expected["score"],
        expected["feedback"],
        expected["comments"],
    )
    assert len((await effects(engine))["review_revisions"]) == 2


async def test_two_distinct_successful_cases_gate_first_grade_and_provisional_probation_once(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "reviewer@example.com")
    competencies, grants, probations = (
        meta_data.tables[name] for name in ("user_competencies", "skill_grants", "probations")
    )
    # An imported numerical baseline is not evidence of two successful cases.
    async with engine.begin() as connection:
        await connection.execute(
            update(competencies)
            .where(competencies.c.user_id == scenario.author_id, competencies.c.competency_id == scenario.skills[0])
            .values(contribution_sum=25, displayed_value=25)
        )
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    first_root = assigned.headers["location"]
    await save(client, first_root, headers, final_draft(score=100))
    first = await complete(client, first_root, headers)
    assert first.status_code == 201, first.text
    async with engine.connect() as connection:
        rows = (
            (await connection.execute(select(competencies).where(competencies.c.user_id == scenario.author_id)))
            .mappings()
            .all()
        )
        assert len(rows) == 4
        assert all(row["grade"] == SkillGrade.PERFORMER for row in rows)
        assert next(row for row in rows if row["competency_id"] == scenario.skills[0])["displayed_value"] == Decimal(
            "39.75"
        )
        assert (
            await connection.scalar(
                select(func.count()).select_from(grants).where(grants.c.user_id == scenario.author_id)
            )
            == 0
        )
        assert await connection.scalar(select(func.count()).select_from(probations)) == 0
    scenario = await second_submission(client, engine, scenario)
    assigned = await claim(client, headers, "second-claim")
    assert assigned.status_code == 201, assigned.text
    second_root = assigned.headers["location"]
    await save(client, second_root, headers, final_draft(score=100))
    second = await complete(client, second_root, headers)
    assert second.status_code == 201, second.text
    facts = await effects(engine)
    author_skills = [row for row in facts["user_competencies"] if row["user_id"] == scenario.author_id]
    author_grants = [row for row in facts["skill_grants"] if row["user_id"] == scenario.author_id]
    assert len(author_skills) == len(author_grants) == len(facts["probations"]) == 4
    assert all(row["grade"] == SkillGrade.REVIEW_MINUS for row in author_skills)
    assert sorted(value(row, "displayed_value", Decimal) for row in author_skills) == [Decimal("29.5")] * 3 + [
        Decimal("54.5")
    ]
    assert all(
        row["state"] == SkillGrantState.PROVISIONAL and row["grade"] == SkillGrade.REVIEW_MINUS for row in author_grants
    )
    assert {row["skill_grant_id"] for row in facts["probations"]} == {row["id"] for row in author_grants}
    assert all(row["required_successes"] == 3 and row["completed_at"] is None for row in facts["probations"])
    assert facts["probation_successes"] == []
    assert len({row["case_id"] for row in facts["progression_contributions"]}) == 2
    assert len(facts["progression_contributions"]) == 8
    assert (await client.get("/api/v1/me/wallet", headers=scenario.author)).json()["settled"] == 16
    for root, created in ((first_root, first), (second_root, second)):
        replay = await complete(client, root, headers, key="repeat-case")
        assert replay.status_code == 200, replay.text
        assert replay.content == created.content
    assert await effects(engine) == facts
    enabled = await client.put(MODE, headers={**scenario.author, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert enabled.status_code == 200


async def test_reciprocal_completions_lock_both_owners_and_preserve_uncapped_xp(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    first = await submitted(client, engine)
    project, second_headers, second_id, skills = await submission_project(
        client, engine, skill_ids=first.skills, email_prefix="reciprocal-"
    )
    head = await edited_solution(client, project, second_headers)
    second = await client.post(
        project + "/submissions",
        headers={**second_headers, "Idempotency-Key": "submit"},
        json={"expected_commit_sha": head},
    )
    assert second.status_code == 201, second.text
    actors = ((first.author_id, first.author), (second_id, second_headers))
    for actor_id, headers in actors:
        await seed_rights(engine, actor_id, skills)
        enabled = await client.put(MODE, headers={**headers, "Idempotency-Key": "enable"}, json={"enabled": True})
        assert enabled.status_code == 200
    async with engine.begin() as connection:
        competencies = meta_data.tables["user_competencies"]
        await connection.execute(
            update(competencies)
            .where(competencies.c.user_id.in_([identifier for identifier, _ in actors]))
            .values(contribution_sum=90, displayed_value=90)
        )
    assigned = await asyncio.gather(*(claim(client, headers) for _, headers in actors))
    assert all(response.status_code == 201 for response in assigned), [response.text for response in assigned]
    assert assigned[0].json()["submission_id"] == second.json()["id"]
    assert assigned[1].json()["submission_id"] == first.submission.rsplit("/", 1)[1]
    for (_, headers), response in zip(actors, assigned, strict=True):
        await save(client, response.headers["location"], headers, final_draft())
    completed = await asyncio.wait_for(
        asyncio.gather(
            *(
                complete(client, response.headers["location"], headers)
                for (_, headers), response in zip(actors, assigned, strict=True)
            )
        ),
        timeout=10,
    )
    assert all(response.status_code == 201 for response in completed), [response.text for response in completed]
    facts = await effects(engine)
    owner_ids = {identifier for identifier, _ in actors}
    for wallet in (row for row in facts["wallets"] if row["owner_id"] in owner_ids):
        assert wallet["settled"] == 18
        assert wallet["karma"] == 1
    values = [row for row in facts["user_competencies"] if row["user_id"] in owner_ids]
    assert len(values) == 8
    assert all(row["displayed_value"] == 100 for row in values)
    # The first publication increases its author's current expertise before that
    # actor finishes the reciprocal review, so the second bonus uses E=100.
    assert sorted({value(row, "contribution_sum", Decimal) for row in values}) == [Decimal("101.8"), Decimal(102)]


__all__ += ["learning_storage"]
