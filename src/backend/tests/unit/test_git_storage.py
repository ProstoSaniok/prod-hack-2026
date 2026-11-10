import asyncio
import base64
import hashlib
import json
from dataclasses import replace

import httpx
import pytest

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.git import GiteaGitStorage
from src.adapters.gitea.transport import GiteaTransport
from src.application.common.containers import SecretString
from src.application.repository_storage.git import (
    GitStorage,
    StorageConflictError,
    StorageValidationError,
    TextFile,
    validate_files,
    validate_path,
)
from src.application.repository_storage.ports import StorageError

_CONFIGURATION = GiteaConfiguration(
    internal_api_url=SecretString("http://private-gitea.invalid"), api_token=SecretString("private-token")
)
_REPO = "/api/v1/repos/service/prodguild-test"
_INITIAL = "a" * 40
_UPDATED = "c" * 40


def _blob(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False).hexdigest()


def _tree(files: dict[str, bytes]) -> str:
    content = b"".join(b"100644 " + path.encode() + b"\0" + bytes.fromhex(_blob(files[path])) for path in sorted(files))
    return hashlib.sha1(b"tree " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False).hexdigest()


class _Gitea:
    """Small wire fixture; live Gitea acceptance is deliberately separate."""

    def __init__(self, *, lose: set[str] | None = None) -> None:
        self.lose = lose or set()
        self.exists = False
        self.repository: dict[str, object] = {
            "id": 12,
            "owner": {"id": 7, "login": "service"},
            "name": "prodguild-test",
            "description": "prodguild:initial",
            "private": True,
            "fork": False,
            "empty": True,
        }
        self.commits: dict[str, dict[str, object]] = {}
        self.trees: dict[str, dict[str, bytes]] = {}
        self.latest_files: dict[str, bytes] = {}
        self.head = _INITIAL
        self.posts: list[str] = []
        self.mode = "100644"
        self.branches: dict[str, str] = {}
        self.protection: dict[str, object] | None = None

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["authorization"] == "token private-token"
        assert "cookie" not in request.headers
        assert "x-unwanted" not in request.headers
        if request.method == "POST":
            return self.mutate(request)
        if path == "/api/v1/user":
            return httpx.Response(200, json={"id": 7, "login": "service"})
        if path == _REPO:
            return httpx.Response(200, json=self.repository) if self.exists else httpx.Response(404)
        if path.startswith(_REPO + "/branches/"):
            branch = path.removeprefix(_REPO + "/branches/")
            sha = self.head if branch == "main" else self.branches.get(branch)
            if sha is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "name": branch,
                    "commit": {"id": sha},
                    "protected": self.protection is not None and branch != "main",
                    "effective_branch_protection_name": branch if self.protection is not None else "",
                },
            )
        if path.startswith(_REPO + "/branch_protections/"):
            return httpx.Response(404) if self.protection is None else httpx.Response(200, json=self.protection)
        if path.startswith(_REPO + "/git/commits/"):
            return httpx.Response(200, json=self.commits[path.rsplit("/", 1)[1]])
        if path.startswith(_REPO + "/git/trees/"):
            sha = path.rsplit("/", 1)[1]
            files = self.trees[sha]
            return httpx.Response(
                200,
                json={
                    "sha": sha,
                    "truncated": False,
                    "total_count": len(files),
                    "tree": [
                        {"path": name, "mode": self.mode, "type": "blob", "sha": _blob(value), "size": len(value)}
                        for name, value in files.items()
                    ]
                    or None,
                },
            )
        if path.startswith(_REPO + "/git/blobs/"):
            sha = path.rsplit("/", 1)[1]
            value = next(value for files in self.trees.values() for value in files.values() if _blob(value) == sha)
            return httpx.Response(
                200,
                json={
                    "sha": sha,
                    "size": len(value),
                    "encoding": "base64",
                    "content": base64.b64encode(value).decode(),
                },
            )
        pytest.fail(f"Unexpected request path: {path}")

    def mutate(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.posts.append(path)
        body = json.loads(request.content)
        if path == "/api/v1/user/repos":
            assert body["private"] is True
            assert body["auto_init"] is False
            self.exists = True
        elif path == _REPO + "/contents":
            parent = None if self.repository["empty"] else self.head
            sha = _INITIAL if parent is None else _UPDATED if parent == _INITIAL else "e" * 40
            files = self.latest_files.copy()
            for file in body["files"]:
                if file["operation"] == "delete":
                    del files[file["path"]]
                else:
                    files[file["path"]] = base64.b64decode(file["content"])
            tree = _tree(files)
            self.commits[sha] = {
                "sha": sha,
                "commit": {"message": body["message"] + "\n", "tree": {"sha": tree}},
                "parents": [{"sha": parent}] if parent else [],
            }
            self.trees[tree] = files
            self.latest_files = files
            self.head = sha
            self.repository["empty"] = False
        elif path == _REPO + "/branches":
            self.branches[body["new_branch_name"]] = body["old_ref_name"]
        elif path == _REPO + "/branch_protections":
            self.protection = body
        else:
            pytest.fail(f"Unexpected mutation path: {path}")
        if path in self.lose:
            self.lose.remove(path)
            raise httpx.ReadError("private-token upstream response lost", request=request)
        return httpx.Response(201, json={})


def test_literal_paths_and_complete_text_snapshot_bounds() -> None:
    for path in ("", "/a", "a/", "a//b", "a/../b", "./a", "a\\b", "a\0b", ".GIT/config", "a/.gIt/b", "é" * 513):
        with pytest.raises(StorageValidationError):
            validate_path(path)
    for path in ("a/b.txt", "%2e%2e/file", "percent%2Fname", "é" * 512):
        validate_path(path)
    for files in (
        (),
        (TextFile("a", b"a"), TextFile("a", b"b")),
        (TextFile("a", b"a"), TextFile("a/b", b"b")),
        (TextFile("a", b"\xff"),),
        (TextFile("a", b"\0"),),
        (TextFile("a", b"a" * 1_048_577),),
        tuple(TextFile(str(index), b"") for index in range(101)),
        tuple(TextFile(prefix + "/d" * 399 + "/file.txt", b"text") for prefix in ("a", "b", "c")),
    ):
        with pytest.raises(StorageValidationError):
            validate_files(files)


async def test_private_initial_snapshot_and_retention_recover_response_loss() -> None:
    fixture = _Gitea(
        lose={"/api/v1/user/repos", _REPO + "/contents", _REPO + "/branches", _REPO + "/branch_protections"}
    )
    files = (TextFile("README.md", b"a" * 70_000), TextFile("note.txt", "Привет".encode()))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fixture.respond), cookies={"unwanted": "value"}, headers={"X-Unwanted": "secret"}
    ) as client:
        storage: GitStorage = GiteaGitStorage(GiteaTransport(client, _CONFIGURATION))
        first = await storage.ensure_snapshot("prodguild-test", "prodguild:initial", files)
        assert first.commit_sha == _INITIAL
        assert await storage.ensure_snapshot("prodguild-test", "prodguild:initial", files) == first
        assert tuple(file.content for file in await storage.files("prodguild-test", _INITIAL)) == tuple(
            file.content for file in files
        )
        await storage.retain("prodguild-test", _INITIAL, "prodguild/bases/example")
        assert fixture.protection is not None
        fixture.protection["merge_whitelist_usernames"] = None
        fixture.protection["merge_whitelist_teams"] = None
        await storage.retain("prodguild-test", _INITIAL, "prodguild/bases/example")
    assert fixture.posts.count("/api/v1/user/repos") == 1
    assert fixture.posts.count(_REPO + "/contents") == 1
    assert fixture.posts.count(_REPO + "/branches") == 1
    assert fixture.posts.count(_REPO + "/branch_protections") == 1


async def test_write_response_loss_recovery_requires_marker_parent_and_entire_tree() -> None:
    fixture = _Gitea()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as client:
        storage = GiteaGitStorage(GiteaTransport(client, _CONFIGURATION))
        await storage.ensure_snapshot("prodguild-test", "prodguild:initial", (TextFile("README.md", b"initial"),))
        fixture.lose.add(_REPO + "/contents")
        first = await storage.write_file(
            "prodguild-test",
            branch="main",
            path="README.md",
            content=b"changed",
            expected_blob_sha=_blob(b"initial"),
            expected_head_sha=_INITIAL,
            marker="prodguild:write:1",
        )
        assert first.commit_sha == _UPDATED
        assert (
            await storage.write_file(
                "prodguild-test",
                branch="main",
                path="README.md",
                content=b"changed",
                expected_blob_sha=_blob(b"initial"),
                expected_head_sha=_INITIAL,
                marker="prodguild:write:1",
            )
            == first
        )
        for marker, content in (("prodguild:other", b"changed"), ("prodguild:write:1", b"different")):
            with pytest.raises(StorageConflictError) as caught:
                await storage.write_file(
                    "prodguild-test",
                    branch="main",
                    path="README.md",
                    content=content,
                    expected_blob_sha=_blob(b"initial"),
                    expected_head_sha=_INITIAL,
                    marker=marker,
                )
            assert caught.value.__context__ is None
        fixture.commits[_UPDATED]["parents"] = []
        with pytest.raises(StorageConflictError):
            await storage.write_file(
                "prodguild-test",
                branch="main",
                path="README.md",
                content=b"changed",
                expected_blob_sha=_blob(b"initial"),
                expected_head_sha=_INITIAL,
                marker="prodguild:write:1",
            )
        fixture.commits[_UPDATED]["parents"] = [{"sha": _INITIAL}]
        deleted = await storage.write_file(
            "prodguild-test",
            branch="main",
            path="README.md",
            content=None,
            expected_blob_sha=_blob(b"changed"),
            expected_head_sha=_UPDATED,
            marker="prodguild:write:2",
        )
        assert deleted.commit_sha == "e" * 40
        assert await storage.files("prodguild-test", deleted.commit_sha) == ()
    assert fixture.posts.count(_REPO + "/contents") == 3


async def test_fail_closed_privacy_ownership_tree_redirect_size_and_deadline() -> None:
    fixture = _Gitea()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as client:
        storage = GiteaGitStorage(GiteaTransport(client, _CONFIGURATION))
        await storage.ensure_snapshot("prodguild-test", "prodguild:initial", (TextFile("README.md", b"initial"),))
        original = fixture.repository.copy()
        for key, value in (
            ("private", False),
            ("fork", True),
            ("owner", {"id": 99, "login": "other"}),
            ("name", "other"),
            ("description", "different marker"),
        ):
            fixture.repository = {**original, key: value}
            with pytest.raises(StorageError) as caught:
                await storage.ensure_snapshot(
                    "prodguild-test", "prodguild:initial", (TextFile("README.md", b"initial"),)
                )
            assert caught.value.__context__ is None
            assert "private-token" not in str(caught.value)
        fixture.repository = original
        for mode in ("120000", "160000"):
            fixture.mode = mode
            with pytest.raises(StorageError):
                await storage.files("prodguild-test", _INITIAL)
    assert len(fixture.posts) == 2

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(200, json={"id": 7, "login": "service"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        storage = GiteaGitStorage(GiteaTransport(client, replace(_CONFIGURATION, request_timeout_seconds=0.05)))
        with pytest.raises(StorageError):
            await storage.snapshot("prodguild-test", "main")
    for response in (
        httpx.Response(302, headers={"Location": "https://external.invalid"}),
        httpx.Response(200, content=b"a" * 2_097_153),
        httpx.Response(200, headers={"Content-Encoding": "unexpected"}),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _, result=response: result)) as client:
            transport = GiteaTransport(client, _CONFIGURATION)
            with pytest.raises(StorageError) as caught:
                await transport.request("GET", "/api/v1/user", max_response_bytes=2_097_152)
            assert caught.value.__context__ is None
