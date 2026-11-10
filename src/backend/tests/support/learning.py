import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.tables import meta_data
from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.git import GiteaGitStorage
from src.adapters.gitea.transport import GiteaTransport
from src.application.common.containers import SecretString
from src.application.repository_storage.git import (
    GitFile,
    GitPullRequest,
    GitSnapshot,
    GitStorage,
    StorageConflictError,
    TextFile,
)
from src.application.repository_storage.ports import StorageIdentity
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc
from tests.support.cases import account


class MemoryGit:
    """CI domain-transaction fixture; live mode below substitutes the real adapter."""

    def __init__(self) -> None:
        self.heads: dict[str, GitSnapshot] = {}
        self.objects: dict[tuple[str, str], tuple[GitFile, ...]] = {}
        self.markers: dict[str, GitSnapshot] = {}
        self.branches: dict[tuple[str, str], str] = {}
        self.pulls: dict[str, GitPullRequest] = {}

    async def identity(self) -> StorageIdentity:
        return StorageIdentity(id=1, login="service")

    def save(self, name: str, marker: str, files: tuple[TextFile, ...]) -> GitSnapshot:
        tree = hashlib.sha1(
            b"".join(file.path.encode() + b"\0" + file.content for file in sorted(files, key=lambda file: file.path)),
            usedforsecurity=False,
        ).hexdigest()
        sha = hashlib.sha1((marker + tree).encode(), usedforsecurity=False).hexdigest()
        before = self.heads.get(name)
        snapshot = GitSnapshot(
            repository_id=before.repository_id if before else len(self.heads) + 1,
            owner_id=1,
            owner="service",
            name=name,
            commit_sha=sha,
            tree_sha=tree,
        )
        self.heads[name] = self.markers[marker] = snapshot
        self.objects[name, sha] = tuple(
            GitFile(
                path=file.path,
                sha=hashlib.sha1(file.content, usedforsecurity=False).hexdigest(),
                size=len(file.content),
                content=file.content,
            )
            for file in files
        )
        return snapshot

    async def ensure_snapshot(
        self, name: str, marker: str, files: tuple[TextFile, ...], *, branch: str = "main"
    ) -> GitSnapshot:
        return self.markers.get(marker) or self.save(name, marker, files)

    async def snapshot(self, name: str, ref: str) -> GitSnapshot:
        if ref == "work":
            return self.heads[name]
        ref = self.branches.get((name, ref), ref)
        return next(item for item in self.markers.values() if item.name == name and item.commit_sha == ref)

    async def files(self, name: str, ref: str) -> tuple[GitFile, ...]:
        return self.objects[name, ref]

    async def retain(self, name: str, commit_sha: str, branch: str) -> None:
        assert (name, commit_sha) in self.objects
        assert self.branches.setdefault((name, branch), commit_sha) == commit_sha

    async def ensure_pull_request(
        self, name: str, *, base_branch: str, base_sha: str, head_branch: str, head_sha: str, marker: str
    ) -> GitPullRequest:
        assert self.branches[name, base_branch] == base_sha
        assert self.branches[name, head_branch] == head_sha
        snapshot = await self.snapshot(name, head_sha)
        return self.pulls.setdefault(
            marker,
            GitPullRequest(
                repository_id=snapshot.repository_id,
                owner_id=1,
                number=1,
                external_id=len(self.pulls) + 1,
                base_sha=base_sha,
                head_sha=head_sha,
            ),
        )

    async def write_file(
        self,
        name: str,
        *,
        branch: str,
        path: str,
        content: bytes | None,
        expected_blob_sha: str | None,
        expected_head_sha: str,
        marker: str,
    ) -> GitSnapshot:
        if marker in self.markers:
            return self.markers[marker]
        if self.heads[name].commit_sha != expected_head_sha:
            raise StorageConflictError
        before = await self.files(name, expected_head_sha)
        current = next((file.sha for file in before if file.path == path), None)
        if current != expected_blob_sha:
            raise StorageConflictError
        files = [TextFile(file.path, file.content) for file in before if file.path != path]
        if content is not None:
            files.append(TextFile(path, content))
        return self.save(name, marker, tuple(files))


@pytest.fixture
async def learning_storage() -> AsyncIterator[GitStorage]:
    configuration = GiteaConfiguration(
        internal_api_url=SecretString(os.getenv("TEST_GITEA_ORIGIN", "")),
        api_token=SecretString(os.getenv("TEST_GITEA_TOKEN", "")),
        request_timeout_seconds=30,
    )
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False), trust_env=False
    ) as upstream:
        yield (
            GiteaGitStorage(GiteaTransport(upstream, configuration)) if os.getenv("TEST_GITEA_ORIGIN") else MemoryGit()
        )


@pytest.fixture
async def learning_client(auth_engine: AsyncEngine, learning_storage: GitStorage) -> AsyncIterator[AsyncClient]:
    configuration = Configuration(
        server=ServerConfiguration(),
        database=DatabaseConfiguration(url=SecretString("unused")),
        access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
        gitea=GiteaConfiguration(
            internal_api_url=SecretString(os.getenv("TEST_GITEA_ORIGIN", "")),
            api_token=SecretString(os.getenv("TEST_GITEA_TOKEN", "")),
            request_timeout_seconds=30,
        ),
    )

    class Dependencies(Provider):
        @provide(scope=Scope.APP, override=True)
        def engine(self) -> AsyncEngine:
            return auth_engine

        @provide(scope=Scope.APP, override=True)
        def git(self) -> GitStorage:
            return learning_storage

    app = make_asgi_application(make_ioc(configuration, Dependencies()))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def participant(client: AsyncClient, engine: AsyncEngine, email: str) -> dict[str, str]:
    _, headers = await account(client, engine, email, organizer=False)
    return headers


async def submission_project(
    client: AsyncClient,
    engine: AsyncEngine,
    *,
    required_value: int = 10,
    author_headers: dict[str, str] | None = None,
    skill_ids: list[UUID] | None = None,
    email_prefix: str = "",
) -> tuple[str, dict[str, str], UUID, list[UUID]]:
    _, owner = await account(client, engine, f"{email_prefix}organizer@example.com")
    student = (
        author_headers
        if author_headers is not None
        else await participant(client, engine, f"{email_prefix}student@example.com")
    )
    user = UUID((await client.get("/auth/me", headers=student)).json()["id"])
    skills = skill_ids if skill_ids is not None else [uuid4() for _ in range(4)]
    if skill_ids is None:
        async with engine.begin() as connection:
            await connection.execute(
                insert(meta_data.tables["competencies"]),
                [
                    {
                        "id": skill,
                        "created_at": datetime.now(UTC),
                        "name": f"Skill {index}",
                        "description": "Snapshot skill",
                    }
                    for index, skill in enumerate(skills)
                ],
            )
    created = await client.post(
        "/api/v1/admin/cases",
        headers={**owner, "Idempotency-Key": "case"},
        json={
            "title": "Submission case",
            "description": "Explain",
            "expected_result": "A solution",
            "competencies": [
                {"competency_id": str(skill), "required_value": required_value, "weight": 0.125} for skill in skills
            ],
        },
    )
    assert created.status_code == 201, created.text
    case = created.headers["location"]
    for version, path in enumerate(("task.txt", "obsolete.txt"), 1):
        uploaded = await client.post(
            case + "/files",
            headers={**owner, "Idempotency-Key": path},
            params={"expected_case_version": version},
            json={"path": path, "content": "Starter\n"},
        )
        assert uploaded.status_code == 201, uploaded.text
    reference = await client.put(
        case + "/reference-solution",
        params={"expected_case_version": 3},
        headers={**owner, "Idempotency-Key": "reference"},
    )
    assert reference.status_code == 201, reference.text
    ready = await client.patch(
        case + "/reference-solution",
        params={"expected_case_version": 4},
        headers={**owner, "Idempotency-Key": "ready"},
        json={"expected_version": 1, "expected_commit_sha": reference.json()["head_commit_sha"], "ready": True},
    )
    assert ready.status_code == 200, ready.text
    published = await client.patch(
        case, headers={**owner, "Idempotency-Key": "publish"}, json={"expected_version": 5, "status": "published"}
    )
    assert published.status_code == 200, published.text
    start = await client.post(
        "/api/v1/me/projects",
        headers={**student, "Idempotency-Key": f"{email_prefix}start"},
        json={"case_id": created.json()["id"]},
    )
    assert start.status_code == 201, start.text
    return start.headers["location"], student, user, skills


async def edited_solution(
    client: AsyncClient, project: str, student: dict[str, str], content: str = "Solution\n"
) -> str:
    before = await client.get(project + "/files/content", params={"path": "task.txt"}, headers=student)
    result = await client.put(
        project + "/files/content",
        params={"path": "task.txt"},
        headers={**student, "Idempotency-Key": "solution"},
        json={"sha": before.json()["sha"], "content": content},
    )
    assert result.status_code == 200, result.text
    return str(result.json()["commit_sha"])


async def native_submission_proof(body: dict[str, object], project: str) -> None:
    if not os.getenv("TEST_GITEA_PEER_TOKEN"):
        return
    async with httpx.AsyncClient(base_url=os.environ["TEST_GITEA_ORIGIN"], trust_env=False) as native:
        service = {"Authorization": "token " + os.environ["TEST_GITEA_TOKEN"]}
        peer = {"Authorization": "token " + os.environ["TEST_GITEA_PEER_TOKEN"]}
        identity = await native.get("/api/v1/user", headers=service)
        assert identity.status_code == 200
        assert (await native.get("/api/v1/user", headers=peer)).status_code == 200
        slug = "prodguild-project-" + UUID(project.rsplit("/", 1)[1]).hex
        root = f"/api/v1/repos/{identity.json()['login']}/{slug}"
        listing = await native.get(root + "/pulls?state=all", headers=service)
        assert listing.status_code == 200
        assert len(listing.json()) == 1
        pull = listing.json()[0]
        assert pull["id"] == body["pull_request_id"]
        assert pull["base"]["repo_id"] == pull["head"]["repo_id"] == body["review_repository_id"]
        assert pull["base"]["sha"] == body["base_commit_sha"]
        assert pull["head"]["sha"] == body["commit_sha"]
        for suffix in (
            f"/pulls/{pull['number']}",
            f"/pulls/{pull['number']}.diff",
            f"/git/commits/{body['commit_sha']}",
        ):
            assert (await native.get(root + suffix, headers=service)).status_code == 200
            assert (await native.get(root + suffix, headers=peer)).status_code == 404
        for side in ("base", "head"):
            denied = await native.delete(root + "/branches/" + pull[side]["ref"], headers=service)
            assert denied.status_code == 403
