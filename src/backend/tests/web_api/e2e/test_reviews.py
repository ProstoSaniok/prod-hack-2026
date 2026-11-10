import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.review_requests import ReviewRequests
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.operations.idempotency import CachedResponse
from src.application.repository_storage.git import GitStorage
from src.application.repository_storage.ports import StorageError
from src.application.review.entity import AssignmentState, JobState
from tests.support.cases import account
from tests.support.learning import MemoryGit, learning_client, participant
from tests.support.review_completion import complete, final_draft, save
from tests.support.reviews import (
    MODE,
    QUEUE,
    claim,
    counts,
    draft_body,
    expire,
    learning_storage,
    reviewer,
    seed_rights,
    submitted,
)

__all__ = ["learning_client", "learning_storage"]


async def test_review_eligibility_requires_participant_confirmed_mode_and_every_snapshot_skill(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    # Emulate a pre-queue submission on an isolated schema: only claim POST repairs it.
    async with engine.begin() as connection:
        await connection.execute(delete(meta_data.tables["review_jobs"]))
    mode = await client.get(MODE, headers=scenario.author)
    assert mode.status_code == 200
    assert mode.json()["can_review"] is False
    available = await client.get(scenario.submission, headers=scenario.author)
    assert available.json()["priority"] == {
        "cost": 5,
        "can_purchase": False,
        "can_cancel": False,
        "current_request": None,
    }
    assert (await claim(client, scenario.author, "performer-denied")).status_code == 403
    assert (await counts(engine))[0] == 0
    assert (await client.get(MODE)).status_code == 401
    user, outsider = await account(client, engine, "no-capability@example.com", organizer=False)
    async with engine.begin() as connection:
        capabilities = meta_data.tables["user_capabilities"]
        await connection.execute(
            update(capabilities).where(capabilities.c.user_id == user).values(revoked_at=datetime.now(UTC))
        )
    await seed_rights(engine, user, scenario.skills)
    assert (
        await client.put(MODE, headers={**outsider, "Idempotency-Key": "enable"}, json={"enabled": True})
    ).status_code == 403
    provisional = await participant(client, engine, "provisional@example.com")
    user = UUID((await client.get("/auth/me", headers=provisional)).json()["id"])
    await seed_rights(engine, user, scenario.skills, state=SkillGrantState.PROVISIONAL)
    assert (
        await client.put(MODE, headers={**provisional, "Idempotency-Key": "enable"}, json={"enabled": True})
    ).status_code == 403
    assert (await client.get(MODE, headers=provisional)).json()["can_review"] is False
    partial = await participant(client, engine, "partial@example.com")
    user = UUID((await client.get("/auth/me", headers=partial)).json()["id"])
    await seed_rights(engine, user, scenario.skills[:-1])
    assert (await client.get(MODE, headers=partial)).json()["can_review"] is True
    disabled = await claim(client, partial, "disabled")
    assert disabled.status_code == 409
    assert disabled.json()["code"] == "REVIEW_MODE_DISABLED"
    enabled = await client.put(MODE, headers={**partial, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    mismatch = await claim(client, partial)
    assert mismatch.status_code == 204
    assert mismatch.headers["x-queue-state"] == "no_eligible"
    await seed_rights(engine, scenario.author_id, scenario.skills, grade=SkillGrade.EXPERT)
    enabled = await client.put(MODE, headers={**scenario.author, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    own = await claim(client, scenario.author)
    assert own.status_code == 204
    assert own.headers["x-queue-state"] == "no_eligible"
    before = await counts(engine)
    for _ in range(2):
        assert (await client.get(QUEUE, headers=partial)).json() == []
        assert (await client.get(MODE, headers=partial)).json()["enabled"] is True
    assert await counts(engine) == before
    user = UUID((await client.get("/auth/me", headers=partial)).json()["id"])
    await seed_rights(engine, user, scenario.skills[-1:])
    cached_empty = await claim(client, partial)
    assert cached_empty.status_code == 204
    assert cached_empty.headers["x-queue-state"] == "no_eligible"
    selected = await claim(client, partial, "all-skills")
    assert selected.status_code == 201, selected.text
    assert selected.json()["submission_id"] == scenario.submission.rsplit("/", 1)[1]
    assert len(selected.json()["reviewer_skills"]) == len(scenario.skills)


async def test_private_pinned_workspace_draft_cas_replay_mode_off_and_revocation(
    learning_client: AsyncClient,
    learning_storage: GitStorage,
    auth_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "reviewer@example.com")
    stranger = await participant(client, engine, "stranger@example.com")
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    task = assigned.json()
    assert task["status"] == "assigned"
    assert task["draft_id"]
    assert task["lease_expires_at"] is None
    assert task["can_continue"] is True
    replay = await claim(client, headers)
    assert replay.status_code == 200
    assert replay.content == assigned.content
    assert replay.headers["location"] == root
    full = await claim(client, headers, "another")
    assert full.status_code == 409
    assert full.json()["code"] == "CAPACITY_LIMIT"
    before = await counts(engine)
    for suffix in ("", "/workspace", "/draft", "/snapshot", "/files", "/diff"):
        response = await client.get(root + suffix, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "PRIVATE EVIDENCE" not in response.text
        assert "PRIVATE SEED" not in response.text
        for other in (scenario.author, stranger):
            assert (await client.get(root + suffix, headers=other)).status_code == 404
    assert await counts(engine) == before
    assert (await client.get(scenario.project + "/files", headers=headers)).status_code == 404
    content = await client.get(root + "/files/content", headers=headers, params={"path": "task.txt"})
    assert content.json()["content"] == "Solution\u2028detail\r\v\n"
    assert isinstance(learning_storage, MemoryGit)
    head = content.json()["commit_sha"]
    name = next(snapshot.name for snapshot in learning_storage.heads.values() if snapshot.commit_sha == head)
    await learning_storage.write_file(
        name,
        branch="work",
        path="task.txt",
        content=b"Later workspace\n",
        expected_blob_sha=content.json()["sha"],
        expected_head_sha=head,
        marker="operator-later-head",
    )
    pinned = await client.get(root + "/files/content", headers=headers, params={"path": "task.txt"})
    assert pinned.content == content.content
    initial = await client.get(root + "/draft", headers=headers)
    assert initial.json()["version"] == 1
    assert initial.json()["comments"] == []
    body = {
        **draft_body(),
        "comments": [
            {"body": "Private deleted-line feedback", "path": "obsolete.txt", "line": 1, "side": "old"},
            {"body": "One LF line", "path": "task.txt", "line": 1, "side": "new"},
        ],
    }
    save_headers = {**headers, "Idempotency-Key": "save"}
    saved = await client.put(root + "/draft", headers=save_headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    with monkeypatch.context() as patch:
        patch.setattr(learning_storage, "files", AsyncMock(side_effect=StorageError))
        replay = await client.put(root + "/draft", headers=save_headers, json=body)
        assert replay.status_code == 200
        assert replay.content == saved.content
    conflict = await client.put(root + "/draft", headers=save_headers, json=draft_body())
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    stale = await client.put(root + "/draft", headers={**headers, "Idempotency-Key": "stale"}, json=body)
    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_VERSION"
    invalid = {**draft_body(2), "comments": [{"body": "Bad line", "path": "task.txt", "line": 2, "side": "new"}]}
    assert (
        await client.put(root + "/draft", headers={**headers, "Idempotency-Key": "invalid"}, json=invalid)
    ).status_code == 422
    # The draft, replacement comments and exact replay receipt are one transaction.
    complete = ReviewRequests.complete

    async def fail_capture(requests: ReviewRequests, response: CachedResponse) -> None:
        await complete(requests, response)
        raise SQLAlchemyError("injected rollback after receipt capture")

    before = await counts(engine)
    with monkeypatch.context() as patch:
        patch.setattr(ReviewRequests, "complete", fail_capture)
        failed = await client.put(
            root + "/draft", headers={**headers, "Idempotency-Key": "replace"}, json=draft_body(2)
        )
        assert failed.status_code == 503
    assert (await client.get(root + "/draft", headers=headers)).content == saved.content
    assert await counts(engine) == before
    off = await client.put(MODE, headers={**headers, "Idempotency-Key": "off"}, json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    replacement = await client.put(
        root + "/draft", headers={**headers, "Idempotency-Key": "replace"}, json=draft_body(2)
    )
    assert replacement.status_code == 200
    assert replacement.json()["version"] == 3
    assert replacement.json()["comments"] == []
    assert "Private deleted-line feedback" not in (await client.get(scenario.submission, headers=scenario.author)).text
    user = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    grants = meta_data.tables["skill_grants"]
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == user)
            .values(state=SkillGrantState.REVOKED, revoked_at=datetime.now(UTC), version=2)
        )
    assert (await client.get(root + "/workspace", headers=headers)).status_code == 403
    assert (await client.get(root + "/draft", headers=headers)).status_code == 403
    assert (await claim(client, headers)).status_code == 403
    assert (await client.put(root + "/draft", headers=save_headers, json=body)).status_code == 403
    tasks = (await client.get(QUEUE, headers=headers)).json()
    assert len(tasks) == 1
    assert tasks[0]["can_continue"] is False
    capabilities = meta_data.tables["user_capabilities"]
    async with engine.begin() as connection:
        await connection.execute(
            update(capabilities).where(capabilities.c.user_id == user).values(revoked_at=datetime.now(UTC))
        )
    assert (await client.get(MODE, headers=headers)).json()["tasks"][0]["can_continue"] is False
    assert (
        await client.delete(root, headers={**headers, "Idempotency-Key": "return-without-rights"})
    ).status_code == 204
    assert (await client.get(QUEUE, headers=headers)).json() == []


async def test_high_case_requirements_need_confirmed_experts_in_every_skill(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine, required_value=75)
    headers = await participant(client, engine, "high-reviewer@example.com")
    user = UUID((await client.get("/auth/me", headers=headers)).json()["id"])
    await seed_rights(engine, user, scenario.skills[:-1], grade=SkillGrade.EXPERT)
    await seed_rights(engine, user, scenario.skills[-1:], grade=SkillGrade.REVIEW_PLUS)
    enabled = await client.put(MODE, headers={**headers, "Idempotency-Key": "enable"}, json={"enabled": True})
    assert enabled.status_code == 200
    denied = await claim(client, headers)
    assert denied.status_code == 204
    assert denied.headers["x-queue-state"] == "no_eligible"
    grants = meta_data.tables["skill_grants"]
    async with engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(grants.c.user_id == user, grants.c.competency_id == scenario.skills[-1])
            .values(state=SkillGrantState.REVOKED, revoked_at=datetime.now(UTC), version=2)
        )
    await seed_rights(engine, user, scenario.skills[-1:], grade=SkillGrade.EXPERT)
    assigned = await claim(client, headers, "qualified")
    assert assigned.status_code == 201, assigned.text
    assert all(item["expert_confirmed"] for item in assigned.json()["reviewer_skills"])


async def test_assignment_has_no_deadline_and_explicit_return_requeues_without_get_writes(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    first = await reviewer(client, engine, scenario.skills, "first@example.com")
    second = await reviewer(client, engine, scenario.skills, "second@example.com")
    assigned = await claim(client, first)
    assert assigned.status_code == 201, assigned.text
    root = assigned.headers["location"]
    await expire(engine, assigned.json()["id"])  # Legacy past timestamp is no longer a deadline.
    before = await counts(engine)
    assert (await client.get(root + "/workspace", headers=first)).status_code == 200
    assert (await claim(client, first)).status_code == 200
    assert len((await client.get(QUEUE, headers=first)).json()) == 1
    assert await counts(engine) == before
    assert (await claim(client, second)).status_code == 204
    assert (await client.delete(root, headers={**second, "Idempotency-Key": "foreign"})).status_code == 404
    for key in ("return", "return", "return-again"):
        returned = await client.delete(root, headers={**first, "Idempotency-Key": key})
        assert returned.status_code == 204, returned.text
    assert (await claim(client, first, "declined")).status_code == 204
    recovered = await claim(client, second, "reassigned")
    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["id"] != assigned.json()["id"]
    assert recovered.json()["submission_id"] == assigned.json()["submission_id"]
    assert (await client.get(recovered.headers["location"] + "/draft", headers=second)).json()["version"] == 1
    assert (await client.get(root + "/draft", headers=first)).status_code == 409
    async with engine.connect() as connection:
        assignments = meta_data.tables["review_assignments"]
        states = (await connection.execute(select(assignments.c.state))).scalars().all()
        assert sorted(states) == sorted([AssignmentState.CANCELLED, AssignmentState.ACTIVE])
        assert await connection.scalar(select(meta_data.tables["review_jobs"].c.state)) == JobState.CLAIMED


async def test_claim_and_draft_races_preserve_single_subject_capacity_and_version(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    reviewers = [await reviewer(client, engine, scenario.skills, f"r{index}@example.com") for index in range(2)]
    responses = await asyncio.gather(*(claim(client, headers) for headers in reviewers))
    assert sorted(response.status_code for response in responses) == [201, 204], str(responses)
    winner = next(index for index, response in enumerate(responses) if response.status_code == 201)
    assigned, headers = responses[winner], reviewers[winner]
    empty = await claim(client, reviewers[1 - winner], "empty")
    assert empty.status_code == 204
    assert empty.headers["x-queue-state"] == "empty"
    assert not empty.content
    await expire(engine, assigned.json()["id"])
    competing = await asyncio.gather(*(claim(client, headers, key) for key in ("capacity-a", "capacity-b")))
    assert sorted(response.status_code for response in competing) == [409, 409], str(competing)
    assert next(response for response in competing if response.status_code == 409).json()["code"] == "CAPACITY_LIMIT"
    current = assigned
    root = current.headers["location"]
    saved = await asyncio.gather(
        *(
            client.put(
                root + "/draft",
                headers={**headers, "Idempotency-Key": key},
                json={**draft_body(), "feedback": {"strengths": key, "problems": "", "next_steps": ""}},
            )
            for key in ("draft-a", "draft-b")
        )
    )
    assert sorted(response.status_code for response in saved) == [200, 409], [response.text for response in saved]
    assert next(response for response in saved if response.status_code == 409).json()["code"] == "STALE_VERSION"
    final = await client.get(root + "/draft", headers=headers)
    assert final.json()["version"] == 2
    assert final.content == next(response for response in saved if response.status_code == 200).content
    async with engine.connect() as connection:
        assignments = meta_data.tables["review_assignments"]
        active = select(func.count()).select_from(assignments).where(assignments.c.state == AssignmentState.ACTIVE)
        assert await connection.scalar(active) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["review_jobs"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["review_drafts"])) == 1


async def test_return_and_completion_race_has_one_terminal_outcome(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client, engine = learning_client, auth_engine
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "race-return@example.com")
    assigned = await claim(client, headers)
    root = assigned.headers["location"]
    await save(client, root, headers, final_draft())
    returned, completed = await asyncio.gather(
        client.delete(root, headers={**headers, "Idempotency-Key": "return"}), complete(client, root, headers)
    )
    assert (returned.status_code, completed.status_code) in {(204, 409), (409, 201)}
    async with engine.connect() as connection:
        assignments, jobs, reviews = (
            meta_data.tables[name] for name in ("review_assignments", "review_jobs", "review_results")
        )
        state = await connection.scalar(
            select(assignments.c.state).where(assignments.c.id == UUID(assigned.json()["id"]))
        )
        assert state in {AssignmentState.CANCELLED, AssignmentState.COMPLETED}
        assert await connection.scalar(select(jobs.c.state)) == (
            JobState.WAITING if state == AssignmentState.CANCELLED else JobState.COMPLETED
        )
        assert await connection.scalar(select(func.count()).select_from(reviews)) == (
            0 if state == AssignmentState.CANCELLED else 1
        )
