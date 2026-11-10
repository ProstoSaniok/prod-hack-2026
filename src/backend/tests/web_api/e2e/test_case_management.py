import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_requests import SqlRequestJournal
from src.application.catalog.entity import RepositoryPurpose
from src.application.project.entity import ProjectState
from src.application.user.entity import UserRole
from tests.support.cases import PATH, account, metadata


async def test_owned_case_crud_replay_and_live_permissions(auth_client: AsyncClient, auth_engine: AsyncEngine) -> None:
    owner, headers = await account(auth_client, auth_engine, "organizer@example.com")
    _, foreign = await account(auth_client, auth_engine, "other@example.com")
    admin_id, admin = await account(auth_client, auth_engine, "admin@example.com", organizer=False)
    async with auth_engine.begin() as connection:
        users = meta_data.tables["users"]
        await connection.execute(update(users).where(users.c.id == admin_id).values(role=UserRole.ADMIN))
    body = await metadata(auth_engine)
    assert (await auth_client.get(PATH)).status_code == 401
    assert (await auth_client.post(PATH, headers={**admin, "Idempotency-Key": "create"}, json=body)).status_code == 403
    create_headers = {**headers, "Idempotency-Key": "create"}
    created = await auth_client.post(PATH, headers=create_headers, json=body)
    assert created.status_code == 201, created.text
    item = created.json()
    url = created.headers["location"]
    assert url == f"{PATH}/{item['id']}"
    assert (item["status"], item["version"], item["difficulty"]) == ("draft", 1, "expert")
    assert not {"reference", "repository", "owner_id"} & item.keys()
    assert created.headers["cache-control"] == "no-store"
    replay = await auth_client.post(PATH, headers=create_headers, json=body)
    assert replay.status_code == 200
    assert replay.content == created.content
    assert replay.headers["location"] == url
    assert (await auth_client.post(PATH, headers=create_headers, json={**body, "title": "Changed"})).status_code == 409
    assert len((await auth_client.get(PATH, headers=headers)).json()) == 1
    assert (await auth_client.get(PATH, headers=foreign)).json() == []
    assert (await auth_client.get(url, headers=foreign)).status_code == 404
    assert (
        await auth_client.patch(
            url, headers={**foreign, "Idempotency-Key": "edit"}, json={"expected_version": 1, "title": "Stolen"}
        )
    ).status_code == 404
    assert (await auth_client.get(url, headers=admin)).status_code == 404
    assert (await auth_client.get(PATH, headers=headers, params={"offset": 2**80})).json() == []
    edit = {"expected_version": 1, "title": "Edited case"}
    edited = await auth_client.patch(url, headers={**headers, "Idempotency-Key": "edit"}, json=edit)
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert (
        await auth_client.patch(url, headers={**headers, "Idempotency-Key": "edit"}, json=edit)
    ).content == edited.content
    assert (await auth_client.patch(url, headers={**headers, "Idempotency-Key": "stale"}, json=edit)).json()[
        "code"
    ] == "STALE_VERSION"
    archived = await auth_client.patch(
        url, headers={**headers, "Idempotency-Key": "archive"}, json={"expected_version": 2, "status": "archived"}
    )
    assert archived.status_code == 200
    assert archived.json()["version"] == 3
    assert (await auth_client.get(PATH, headers=headers, params={"status": "draft"})).json() == []
    assert len((await auth_client.get(PATH, headers=headers, params={"status": "archived"})).json()) == 1
    deleted = await auth_client.delete(
        url, headers={**headers, "Idempotency-Key": "delete"}, params={"expected_version": 3}
    )
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert (
        await auth_client.delete(url, headers={**headers, "Idempotency-Key": "delete"}, params={"expected_version": 3})
    ).status_code == 204
    assert (await auth_client.get(url, headers=headers)).status_code == 404
    assert (await auth_client.post(PATH, headers=create_headers, json=body)).status_code == 404
    async with auth_engine.begin() as connection:
        grants = meta_data.tables["user_capabilities"]
        await connection.execute(update(grants).where(grants.c.user_id == owner).values(revoked_at=datetime.now(UTC)))
    assert (await auth_client.get(PATH, headers=headers)).status_code == 403


async def test_case_validation_decimal_precision_and_first_project_freeze(
    auth_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    owner, headers = await account(auth_client, auth_engine, "owner@example.com")
    body = await metadata(auth_engine)
    assert (await auth_client.post(PATH, headers=headers, json=body)).status_code == 422
    mutation = {**headers, "Idempotency-Key": "validation"}
    oversized = await auth_client.post(PATH, headers=mutation, content=b"x" * 65_537)
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "PAYLOAD_TOO_LARGE"
    duplicate = await auth_client.post(
        PATH, headers={**mutation, "Content-Type": "application/json"}, content=b'{"title":"one","title":"two"}'
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "VALIDATION_ERROR"
    skills = body["competencies"]
    assert isinstance(skills, list)
    for changed in (
        {"title": "x" * 501},
        {"status": "published"},
        {"difficulty": "review_minus"},
        {"owner_id": str(uuid4())},
        {"competencies": skills * 2},
        {"competencies": [{"competency_id": str(uuid4()), "required_value": 75, "weight": 0.25}]},
    ):
        invalid = await auth_client.post(PATH, headers=mutation, json={**body, **changed})
        assert invalid.status_code == 422, invalid.text
    raw = json.dumps(body).replace('"weight": 0.25', '"weight": 0.1000000000000000000000000001')
    created = await auth_client.post(PATH, headers={**mutation, "Content-Type": "application/json"}, content=raw)
    assert created.status_code == 201, created.text
    assert json.loads(created.content, parse_float=Decimal)["competencies"][0]["weight"] == Decimal(
        "0.1000000000000000000000000001"
    )
    url = created.headers["location"]
    assert (
        await auth_client.patch(
            url, headers={**headers, "Idempotency-Key": "null"}, json={"expected_version": 1, "title": None}
        )
    ).status_code == 422
    publishing = await auth_client.patch(
        url, headers={**headers, "Idempotency-Key": "publish"}, json={"expected_version": 1, "status": "published"}
    )
    assert publishing.status_code == 409
    assert publishing.json()["code"] == "CASE_NOT_READY"
    repository = uuid4()
    async with auth_engine.begin() as connection:
        await connection.execute(
            insert(meta_data.tables["repositories"]).values(
                id=repository,
                created_at=datetime.now(UTC),
                owner_id=owner,
                provider="fixture",
                external_id="workspace",
                purpose=RepositoryPurpose.WORKSPACE,
            )
        )
        await connection.execute(
            insert(meta_data.tables["projects"]).values(
                id=uuid4(),
                created_at=datetime.now(UTC),
                participant_id=owner,
                case_id=UUID(created.json()["id"]),
                case_version=1,
                repository_id=repository,
                repository_purpose=RepositoryPurpose.WORKSPACE,
                working_branch="work",
                state=ProjectState.ACTIVE,
            )
        )
    frozen = await auth_client.patch(
        url, headers={**headers, "Idempotency-Key": "frozen"}, json={"expected_version": 1, "title": "Unsafe edit"}
    )
    assert frozen.status_code == 409
    assert frozen.json()["code"] == "CASE_FROZEN"
    assert (
        await auth_client.delete(
            url, headers={**headers, "Idempotency-Key": "frozen-delete"}, params={"expected_version": 1}
        )
    ).status_code == 409
    assert (
        await auth_client.patch(
            url, headers={**headers, "Idempotency-Key": "hide"}, json={"expected_version": 1, "status": "archived"}
        )
    ).status_code == 200


async def test_case_journal_and_content_commit_together_and_concurrent_edits_conflict(
    auth_client: AsyncClient,
    auth_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, headers = await account(auth_client, auth_engine, "atomic@example.com")
    body = await metadata(auth_engine)

    async def fail_capture(*args: object, **kwargs: object) -> bool:
        raise SQLAlchemyError("private database failure")

    with monkeypatch.context() as patch:
        patch.setattr(SqlRequestJournal, "complete", fail_capture)
        failed = await auth_client.post(PATH, headers={**headers, "Idempotency-Key": "retry"}, json=body)
        assert failed.status_code == 503
        assert "private database" not in failed.text
    async with auth_engine.connect() as connection:
        for name in ("cases", "case_competencies", "request_scopes", "request_generations"):
            assert await connection.scalar(select(func.count()).select_from(meta_data.tables[name])) == 0
    duplicates = await asyncio.gather(
        *(auth_client.post(PATH, headers={**headers, "Idempotency-Key": "retry"}, json=body) for _ in range(2))
    )
    assert sorted(response.status_code for response in duplicates) == [200, 201]
    assert duplicates[0].content == duplicates[1].content
    url = duplicates[0].headers["location"]
    edits = await asyncio.gather(
        *(
            auth_client.patch(
                url,
                headers={**headers, "Idempotency-Key": f"edit-{i}"},
                json={"expected_version": 1, "title": f"Edit {i}"},
            )
            for i in range(2)
        )
    )
    assert sorted(response.status_code for response in edits) == [200, 409]
    assert (await auth_client.get(url, headers=headers)).json()["version"] == 2


async def test_incomplete_draft_identity_survives_new_keys_and_keeps_edits(
    auth_client: AsyncClient, auth_engine: AsyncEngine
) -> None:
    _, headers = await account(auth_client, auth_engine, "draft@example.com")
    _, foreign = await account(auth_client, auth_engine, "foreign-draft@example.com")
    identity = str(uuid4())
    body = {"id": identity, "title": "", "description": "", "expected_result": "", "competencies": []}
    responses = await asyncio.gather(
        *(
            auth_client.post(PATH, headers={**headers, "Idempotency-Key": f"draft-{index}"}, json=body)
            for index in range(2)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert all(response.json()["id"] == identity for response in responses)
    url = f"{PATH}/{identity}"
    assert len((await auth_client.get(PATH, headers=headers)).json()) == 1
    assert (
        await auth_client.post(PATH, headers={**foreign, "Idempotency-Key": "foreign"}, json=body)
    ).status_code == 404
    edited = await auth_client.patch(
        url,
        headers={**headers, "Idempotency-Key": "edit-draft"},
        json={"expected_version": 1, "title": "My draft", "competencies": []},
    )
    assert edited.status_code == 200
    retried = await auth_client.post(PATH, headers={**headers, "Idempotency-Key": "later"}, json=body)
    assert retried.status_code == 200
    assert retried.json()["title"] == "My draft"
    assert retried.json()["version"] == 2
    obsolete = await auth_client.post(
        PATH,
        headers={**headers, "Idempotency-Key": "obsolete-create"},
        json={
            **body,
            "competencies": [{"competency_id": str(uuid4()), "required_value": 0, "weight": 0.25}],
        },
    )
    assert obsolete.status_code == 200
    assert obsolete.json() == retried.json()
    published = await auth_client.patch(
        url,
        headers={**headers, "Idempotency-Key": "publish-draft"},
        json={"expected_version": 2, "status": "published"},
    )
    assert published.status_code == 409
    assert published.json()["code"] == "CASE_NOT_READY"
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(meta_data.tables["git_learning_jobs"])) == 0
    assert (
        await auth_client.delete(
            url, headers={**headers, "Idempotency-Key": "delete-draft"}, params={"expected_version": 2}
        )
    ).status_code == 204
    assert (
        await auth_client.post(PATH, headers={**headers, "Idempotency-Key": "deleted"}, json=body)
    ).status_code == 404
