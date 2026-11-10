from tests.support.learning import learning_client, learning_storage

__all__ = ["learning_client", "learning_storage"]

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways import learning as learning_gateway
from src.adapters.data_gateways.learning import DefaultLearningStore
from src.adapters.data_gateways.submission import finalize_submission
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import ReadinessDecision, SkillGrade, SkillGrantEvidenceKind, SkillGrantState
from src.application.operations.idempotency import CachedResponse
from src.application.project.learning import GitPlan
from src.application.repository_storage.git import (
    GitPullRequest,
    GitSnapshot,
    GitStorage,
)
from tests.support.cases import account, metadata
from tests.support.learning import MemoryGit, edited_solution, native_submission_proof, participant, submission_project


async def test_published_case_private_reference_and_owned_project_files(
    learning_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    client = learning_client
    _, owner = await account(client, auth_engine, "organizer@example.com")
    student = await participant(client, auth_engine, "student@example.com")
    foreign = await participant(client, auth_engine, "foreign@example.com")
    body = await metadata(auth_engine)
    created = await client.post("/api/v1/admin/cases", headers={**owner, "Idempotency-Key": "case"}, json=body)
    assert created.status_code == 201, created.text
    case = created.headers["location"]
    case_id = created.json()["id"]
    uploaded = await client.post(
        case + "/files",
        params={"expected_case_version": 1},
        headers={**owner, "Idempotency-Key": "material"},
        json={"path": "src/task.txt", "content": "Implement a solution"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["content"] is None
    assert (await client.get("/api/v1/cases", headers=student)).json() == []
    reference = await client.put(
        case + "/reference-solution",
        params={"expected_case_version": 2},
        headers={**owner, "Idempotency-Key": "reference"},
    )
    assert reference.status_code == 201, reference.text
    private_file = await client.get(
        case + "/reference-solution/files/content", params={"path": "src/task.txt"}, headers=owner
    )
    edited = await client.put(
        case + "/reference-solution/files/content",
        params={"path": "src/task.txt", "expected_case_version": 3, "expected_reference_version": 1},
        headers={**owner, "Idempotency-Key": "solution"},
        json={"sha": private_file.json()["sha"], "content": "PRIVATE REFERENCE SOLUTION"},
    )
    assert edited.status_code == 200, edited.text
    current_reference = (await client.get(case + "/reference-solution", headers=owner)).json()
    assert current_reference["ready"] is False
    ready = await client.patch(
        case + "/reference-solution",
        params={"expected_case_version": 4},
        headers={**owner, "Idempotency-Key": "ready"},
        json={"expected_version": 2, "expected_commit_sha": current_reference["head_commit_sha"], "ready": True},
    )
    assert ready.status_code == 200, ready.text
    published = await client.patch(
        case, headers={**owner, "Idempotency-Key": "publish"}, json={"expected_version": 5, "status": "published"}
    )
    assert published.status_code == 200, published.text
    assert (await client.get(case + "/reference-solution", headers=student)).status_code == 404
    public = await client.get(
        f"/api/v1/cases/{case_id}/files/content", headers=student, params={"path": "src/task.txt"}
    )
    assert public.status_code == 200, public.text
    assert public.json()["content"] == "Implement a solution"
    assert len((await client.get("/api/v1/cases", headers=student)).json()) == 1
    started = await client.post(
        "/api/v1/me/projects", headers={**student, "Idempotency-Key": "start"}, json={"case_id": case_id}
    )
    assert started.status_code == 201, started.text
    project = started.headers["location"]
    repeated = await client.post(
        "/api/v1/me/projects", headers={**student, "Idempotency-Key": "different-key"}, json={"case_id": case_id}
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == started.json()["id"]
    assert (await client.get(project, headers=foreign)).status_code == 404
    assert len((await client.get("/api/v1/me/projects", headers=student)).json()) == 1
    file = await client.get(project + "/files/content", headers=student, params={"path": "src/task.txt"})
    assert file.json()["content"] == "Implement a solution"
    payload = {"content": "My own solution", "sha": file.json()["sha"]}
    changed = await client.put(
        project + "/files/content",
        headers={**student, "Idempotency-Key": "work"},
        params={"path": "src/task.txt"},
        json=payload,
    )
    assert changed.status_code == 200, changed.text
    assert (
        await client.put(
            project + "/files/content",
            headers={**student, "Idempotency-Key": "work"},
            params={"path": "src/task.txt"},
            json=payload,
        )
    ).content == changed.content
    assert (
        await client.put(
            project + "/files/content",
            headers={**student, "Idempotency-Key": "stale"},
            params={"path": "src/task.txt"},
            json=payload,
        )
    ).status_code == 409
    assert (await client.get(project + "/files/content", headers=student, params={"path": "src/task.txt"})).json()[
        "content"
    ] == "My own solution"
    races = await asyncio.gather(
        *(
            client.put(
                project + "/files/content",
                headers={**student, "Idempotency-Key": f"race-{index}"},
                params={"path": "src/task.txt"},
                json={"content": f"Concurrent {index}", "sha": changed.json()["sha"]},
            )
            for index in range(2)
        )
    )
    assert sorted(response.status_code for response in races) == [200, 409]
    original_start = await client.post(
        "/api/v1/me/projects", headers={**student, "Idempotency-Key": "start"}, json={"case_id": case_id}
    )
    assert original_start.content == started.content
    immutable_version = (await client.get(f"/api/v1/cases/{case_id}", headers=student)).json()["content_version"]
    frozen_version = (await client.get(case, headers=owner)).json()["version"]
    frozen = await client.patch(
        case,
        headers={**owner, "Idempotency-Key": "frozen"},
        json={"expected_version": frozen_version, "title": "Forbidden edit"},
    )
    assert frozen.status_code == 409
    assert frozen.json()["code"] == "CASE_FROZEN"
    archived = await client.patch(
        case,
        headers={**owner, "Idempotency-Key": "archive"},
        json={"expected_version": frozen_version, "status": "archived"},
    )
    assert archived.status_code == 200
    assert (await client.get(f"/api/v1/cases/{case_id}", headers=student)).status_code == 404
    assert (await client.get(project + "/files", headers=student)).status_code == 200
    restored = await client.patch(
        case,
        headers={**owner, "Idempotency-Key": "restore"},
        json={"expected_version": archived.json()["version"], "status": "published"},
    )
    assert restored.status_code == 200, restored.text
    assert (await client.get(f"/api/v1/cases/{case_id}", headers=student)).json()[
        "content_version"
    ] == immutable_version
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["projects"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["project_start_results"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["project_file_results"])) == 2
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["case_content_versions"])) == 1
        workspaces = (await connection.execute(select(meta_data.tables["git_workspaces"].c.name))).scalars().all()
    if os.getenv("TEST_GITEA_PEER_TOKEN"):
        async with httpx.AsyncClient(base_url=os.environ["TEST_GITEA_ORIGIN"], trust_env=False) as native:
            service_headers = {"Authorization": "token " + os.environ["TEST_GITEA_TOKEN"]}
            peer_headers = {"Authorization": "token " + os.environ["TEST_GITEA_PEER_TOKEN"]}
            identity = await native.get("/api/v1/user", headers=service_headers)
            assert identity.status_code == 200
            assert (await native.get("/api/v1/user", headers=peer_headers)).status_code == 200
            for name in workspaces:
                url = f"/api/v1/repos/{identity.json()['login']}/{name}"
                positive = await native.get(url, headers=service_headers)
                assert positive.status_code == 200
                assert positive.json()["private"] is True
                assert positive.json()["fork"] is False
                assert (await native.get(url, headers=peer_headers)).status_code == 404


async def test_git_effect_survives_database_finalizer_failure(
    learning_client: AsyncClient, auth_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = learning_client
    _, owner = await account(client, auth_engine, "owner@example.com")
    created = await client.post(
        "/api/v1/admin/cases", headers={**owner, "Idempotency-Key": "case"}, json=await metadata(auth_engine)
    )
    case = created.headers["location"]
    headers = {**owner, "Idempotency-Key": "recover-file"}
    payload = {"path": "task.txt", "content": "Persisted input"}

    async def fail(*args: object, **kwargs: object) -> CachedResponse:
        raise SQLAlchemyError("private SQL detail")

    with monkeypatch.context() as patch:
        patch.setattr(DefaultLearningStore, "finish", fail)
        failed = await client.post(case + "/files", headers=headers, params={"expected_case_version": 1}, json=payload)
        assert failed.status_code == 503
        assert "private SQL" not in failed.text
    blocked = await client.patch(
        case, headers={**owner, "Idempotency-Key": "blocked"}, json={"expected_version": 1, "title": "Should wait"}
    )
    assert blocked.status_code == 409
    async with auth_engine.begin() as connection:
        operations = meta_data.tables["operations"]
        await connection.execute(update(operations).values(lease_until=func.clock_timestamp()))
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["git_learning_jobs"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["repositories"])) == 0
    recovered = await client.post(case + "/files", headers=headers, params={"expected_case_version": 1}, json=payload)
    assert recovered.status_code == 201, recovered.text
    replay = await client.post(case + "/files", headers=headers, params={"expected_case_version": 1}, json=payload)
    assert replay.status_code == 200
    assert replay.content == recovered.content
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["repositories"])) == 1
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["git_learning_results"])) == 1
    deleted = await client.delete(
        case + "/files/content",
        headers={**owner, "Idempotency-Key": "delete-last"},
        params={"path": "task.txt", "sha": recovered.json()["sha"], "expected_case_version": 2},
    )
    assert deleted.status_code == 204, deleted.text
    empty_reference = await client.put(
        case + "/reference-solution",
        headers={**owner, "Idempotency-Key": "empty-reference"},
        params={"expected_case_version": 3},
    )
    assert empty_reference.status_code == 422, empty_reference.text
    replacement = await client.post(
        case + "/files",
        headers={**owner, "Idempotency-Key": "replace-materials"},
        params={"expected_case_version": 3},
        json=payload,
    )
    assert replacement.status_code == 201, replacement.text


async def test_submission_snapshot_replay_and_current_authorization(
    learning_client: AsyncClient,
    learning_storage: GitStorage,
    auth_engine: AsyncEngine,
) -> None:
    client = learning_client
    project, student, user, skills = await submission_project(client, auth_engine)
    foreign = await participant(client, auth_engine, "foreign@example.com")
    initial = (await client.get(project, headers=student)).json()["head_commit_sha"]
    url, headers = project + "/submissions", {**student, "Idempotency-Key": "submit"}
    unchanged = await client.post(url, headers=headers, json={"expected_commit_sha": initial})
    assert unchanged.status_code == 409
    assert unchanged.json()["code"] == "NO_CHANGES"
    assert (
        await client.post(url, headers=headers, json={"expected_commit_sha": initial, "summary": None})
    ).status_code == 422
    head = await edited_solution(client, project, student, "Solution\u2028detail\r\v\n")
    stale = await client.post(url, headers=headers, json={"expected_commit_sha": initial})
    assert stale.status_code == 409
    assert stale.json()["code"] == "COMMIT_CHANGED"
    old = await client.get(project + "/files/content", params={"path": "obsolete.txt"}, headers=student)
    deleted = await client.delete(
        project + "/files/content",
        params={"path": "obsolete.txt", "sha": old.json()["sha"]},
        headers={**student, "Idempotency-Key": "remove"},
    )
    assert deleted.status_code == 204
    head = (await client.get(project, headers=student)).json()["head_commit_sha"]
    now, grant_id = datetime.now(UTC), uuid4()
    async with auth_engine.begin() as connection:
        for skill, grade, number in (
            (skills[0], SkillGrade.PERFORMER, 98),
            (skills[1], SkillGrade.EXPERT, 1),
            (skills[2], SkillGrade.PERFORMER, 0),
        ):
            await connection.execute(
                insert(meta_data.tables["user_competencies"]).values(
                    id=uuid4(),
                    created_at=now,
                    user_id=user,
                    competency_id=skill,
                    contribution_sum=number,
                    displayed_value=number,
                    grade=grade,
                    version=3,
                )
            )
        for skill, grade, state, identifier in (
            (skills[1], SkillGrade.EXPERT, SkillGrantState.CONFIRMED, grant_id),
            (skills[2], SkillGrade.REVIEW_MINUS, SkillGrantState.REVOKED, uuid4()),
        ):
            await connection.execute(
                insert(meta_data.tables["skill_grants"]).values(
                    id=identifier,
                    created_at=now,
                    user_id=user,
                    competency_id=skill,
                    user_competency_version=1,
                    grade=grade,
                    state=state,
                    evidence_kind=SkillGrantEvidenceKind.SEED,
                    evidence="INTERNAL EVIDENCE",
                    seed_reference="PRIVATE SEED",
                    version=2,
                    confirmed_at=now,
                    revoked_at=now if state == SkillGrantState.REVOKED else None,
                    readiness_decision=ReadinessDecision.CONFIRMED,
                    confirmed_by_grade=SkillGrade.EXPERT,
                )
            )
    payload = {"expected_commit_sha": head, "summary": ""}
    async with auth_engine.begin() as connection:
        competencies = meta_data.tables["user_competencies"]
        await connection.execute(
            update(competencies).where(competencies.c.competency_id == skills[0]).values(deleted_at=now)
        )
    unavailable = await client.post(url, headers=headers, json=payload)
    assert unavailable.status_code == 409
    assert unavailable.json()["code"] == "SKILL_UNAVAILABLE"
    async with auth_engine.begin() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["submission_intents"])) == 0
        await connection.execute(
            update(competencies).where(competencies.c.competency_id == skills[0]).values(deleted_at=None)
        )
    assert (await client.post(url, headers={**foreign, "Idempotency-Key": "foreign"}, json=payload)).status_code == 404
    submitted = await client.post(url, headers=headers, json=payload)
    assert submitted.status_code == 201, submitted.text
    location = submitted.headers["location"]
    assert submitted.headers["cache-control"] == "no-store"
    assert "INTERNAL EVIDENCE" not in submitted.text
    assert "PRIVATE SEED" not in submitted.text
    replay = await client.post(url, headers={**student, "Idempotency-Key": "another"}, json=payload)
    assert replay.status_code == 200
    assert replay.content == submitted.content
    mismatch = await client.post(url, headers=headers, json={"expected_commit_sha": head})
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_CONFLICT"
    changed = await client.post(
        url, headers={**student, "Idempotency-Key": "changed"}, json={"expected_commit_sha": head}
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "PROJECT_SUBMITTED"
    assert (await client.get(location, headers=foreign)).status_code == 404
    assert (await client.get(location)).status_code == 401
    assert (await client.get("/api/v1/me/submissions", headers=foreign)).json() == []
    assert len((await client.get("/api/v1/me/submissions", headers=student)).json()) == 1
    assert (await client.get(project, headers=student)).json()["submission_id"] == submitted.json()["id"]
    blocked = await client.post(
        project + "/files",
        headers={**student, "Idempotency-Key": "blocked"},
        json={"path": "later.txt", "content": "No more edits"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PROJECT_SUBMITTED"
    async with auth_engine.begin() as connection:
        snapshots = meta_data.tables["submission_competencies"]
        facts = (await connection.execute(select(snapshots).order_by(snapshots.c.competency_id))).mappings().all()
        by_skill = {row["competency_id"]: row for row in facts}
        assert len(facts) == 4
        assert by_skill[skills[0]]["author_grade"] == SkillGrade.PERFORMER
        assert by_skill[skills[0]]["author_value"] == 98
        assert by_skill[skills[1]]["author_grade"] == SkillGrade.EXPERT
        assert by_skill[skills[1]]["author_grant_user_competency_version"] == 1
        assert by_skill[skills[1]]["author_user_competency_version"] == 3
        assert by_skill[skills[2]]["author_grant_state"] == SkillGrantState.REVOKED
        assert by_skill[skills[3]]["author_value"] == 0
        assert by_skill[skills[3]]["author_grant_id"] is None
        await connection.execute(update(meta_data.tables["user_competencies"]).values(displayed_value=42, version=4))
        grants = meta_data.tables["skill_grants"]
        await connection.execute(
            update(grants)
            .where(grants.c.id == grant_id)
            .values(state=SkillGrantState.REVOKED, revoked_at=now, version=3)
        )
        workspaces = meta_data.tables["git_workspaces"]
        projects = meta_data.tables["projects"]
        name = await connection.scalar(
            select(workspaces.c.name)
            .join(projects, projects.c.repository_id == workspaces.c.repository_id)
            .where(projects.c.id == UUID(project.rsplit("/", 1)[1]))
        )
        assert isinstance(name, str)
    current = (await client.get(location, headers=student)).json()
    assert current.pop("priority")["current_request"] is None
    assert current == submitted.json()
    current_files = await learning_storage.files(name, head)
    await learning_storage.write_file(
        name,
        branch="work",
        path="task.txt",
        content=b"Later workspace\n",
        expected_blob_sha=next(file.sha for file in current_files if file.path == "task.txt"),
        expected_head_sha=head,
        marker=f"operator-test:{uuid4()}",
    )
    current = (await client.get(location, headers=student)).json()
    assert current.pop("priority")["current_request"] is None
    assert current == submitted.json()
    pinned = await client.get(location + "/files/content", headers=student, params={"path": "task.txt"})
    assert pinned.status_code == 200
    assert pinned.json()["content"] == "Solution\u2028detail\r\v\n"
    assert pinned.json()["commit_sha"] == head
    assert (
        await client.get(location + "/files/content", headers=student, params={"path": "obsolete.txt"})
    ).status_code == 404
    base = await client.get(
        location + "/files/content", headers=student, params={"path": "obsolete.txt", "side": "base"}
    )
    assert base.json()["content"] == "Starter\n"
    assert base.json()["commit_sha"] == initial
    snapshot = await client.get(location + "/snapshot", headers=student)
    assert snapshot.json()["files"][0]["content"] == "Solution\u2028detail\r\v\n"
    diff = await client.get(location + "/diff", headers=student)
    assert diff.json()["base_commit_sha"] == initial
    assert diff.json()["head_commit_sha"] == head
    assert [(entry["comment_path"], entry["status"]) for entry in diff.json()["entries"]] == [
        ("obsolete.txt", "deleted"),
        ("task.txt", "modified"),
    ]
    lines = await client.get(location + "/diff/lines", headers=student, params={"path": "obsolete.txt", "limit": 1})
    assert lines.json()["lines"] == [
        {"kind": "deletion", "old_line": 1, "new_line": None, "text": "Starter", "text_truncated": False}
    ]
    task_lines = await client.get(location + "/diff/lines", headers=student, params={"path": "task.txt"})
    assert task_lines.json()["lines"] == [
        {"kind": "deletion", "old_line": 1, "new_line": None, "text": "Starter", "text_truncated": False},
        {
            "kind": "addition",
            "old_line": None,
            "new_line": 1,
            "text": "Solution\u2028detail\r\v",
            "text_truncated": False,
        },
    ]
    for suffix in ("/snapshot", "/files", "/diff", "/files/content?path=task.txt", "/diff/lines?path=task.txt"):
        assert (await client.get(location + suffix, headers=foreign)).status_code == 404
    await native_submission_proof(submitted.json(), project)
    async with auth_engine.begin() as connection:
        generations, scopes = meta_data.tables["request_generations"], meta_data.tables["request_scopes"]
        request = await connection.scalar(
            select(scopes.c.current_request_id).where(
                scopes.c.actor_id == user, scopes.c.path == url, scopes.c.request_key == "submit"
            )
        )
        assert request is not None
        # Advance this synthetic receipt's age without changing its immutable result or binding.
        await connection.execute(
            update(generations)
            .where(generations.c.id == request)
            .values(created_at=now - timedelta(days=3), expires_at=now - timedelta(days=1))
        )
    expired = await client.post(url, headers=headers, json=payload)
    assert expired.status_code == 200
    assert expired.content == submitted.content
    async with auth_engine.begin() as connection:
        capabilities = meta_data.tables["user_capabilities"]
        await connection.execute(update(capabilities).where(capabilities.c.user_id == user).values(revoked_at=now))
    assert (await client.post(url, headers=headers, json=payload)).status_code == 403
    assert (await client.get(location, headers=student)).status_code == 403


async def test_submission_recovers_one_pr_after_sql_rollback(
    learning_client: AsyncClient,
    learning_storage: GitStorage,
    auth_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = learning_client
    project, student, _, _ = await submission_project(client, auth_engine)
    head = await edited_solution(client, project, student)
    url, payload = project + "/submissions", {"expected_commit_sha": head}
    headers = {**student, "Idempotency-Key": "recover"}

    async def fail(
        store: DefaultLearningStore, plan: GitPlan, snapshot: GitSnapshot, pull: GitPullRequest | None
    ) -> CachedResponse:
        await finalize_submission(store, plan, snapshot, pull)
        raise SQLAlchemyError("private finalization detail")

    with monkeypatch.context() as patch:
        patch.setattr(learning_gateway, "finalize_submission", fail)
        failed = await client.post(url, headers=headers, json=payload)
        assert failed.status_code == 503
        assert "private finalization" not in failed.text
    waiting = await client.post(
        project + "/files",
        headers={**student, "Idempotency-Key": "wait"},
        json={"path": "another.txt", "content": "Must wait"},
    )
    assert waiting.status_code == 409
    assert waiting.json()["code"] == "OPERATION_IN_PROGRESS"
    async with auth_engine.begin() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["submissions"])) == 0
        assert (
            await connection.scalar(select(func.count()).select_from(meta_data.tables["submission_competencies"])) == 0
        )
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["user_competencies"])) == 0
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["submission_intents"])) == 1
        operations = meta_data.tables["operations"]
        await connection.execute(
            update(operations)
            .where(operations.c.kind == "learning.project.submit")
            .values(lease_until=func.clock_timestamp())
        )
    recovered = await asyncio.gather(
        *(
            client.post(url, headers={**student, "Idempotency-Key": key}, json=payload)
            for key in ("recover", "parallel")
        )
    )
    created = next(response for response in recovered if response.status_code == 201)
    assert all(response.status_code in {200, 201, 409} for response in recovered)
    replay = await client.post(url, headers=headers, json=payload)
    assert replay.status_code == 200
    assert replay.content == created.content
    async with auth_engine.connect() as connection:
        for name in ("submissions", "submission_intents", "submission_results", "submission_git_bindings"):
            assert await connection.scalar(select(func.count()).select_from(meta_data.tables[name])) == 1
    if isinstance(learning_storage, MemoryGit):
        assert len(learning_storage.pulls) == 1
    await native_submission_proof(created.json(), project)
