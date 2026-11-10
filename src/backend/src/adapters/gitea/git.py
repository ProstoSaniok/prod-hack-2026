import asyncio
import base64
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from functools import wraps
from typing import Annotated, Literal, overload
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from src.adapters.gitea.transport import GiteaTransport
from src.application.repository_storage.git import (
    MAX_FILES,
    MAX_TEXT_BYTES,
    MAX_TREE_ENTRIES,
    GitFile,
    GitPullRequest,
    GitSnapshot,
    StorageConflictError,
    StorageValidationError,
    TextFile,
    validate_files,
    validate_path,
)
from src.application.repository_storage.ports import StorageError, StorageIdentity

type _SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
type _Positive = Annotated[int, Field(gt=0)]
type _Size = Annotated[int, Field(ge=0, le=MAX_TEXT_BYTES)]


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)


class _Owner(_Model):
    id: _Positive
    login: str


class _Repository(_Model):
    id: _Positive
    owner: _Owner
    name: str
    description: str
    private: Literal[True]
    fork: Literal[False]
    empty: bool


class _PullRepository(_Model):
    id: _Positive
    owner: _Owner
    name: str
    private: bool
    fork: bool


class _PullRef(_Model):
    ref: str
    sha: _SHA
    repo_id: _Positive
    repo: _PullRepository


class _PullRequest(_Model):
    id: _Positive
    number: _Positive
    user: _Owner
    title: str
    body: str | None
    state: Literal["open", "closed"]
    merged: bool
    base: _PullRef
    head: _PullRef


_PULL_PAGE: TypeAdapter[list[_PullRequest]] = TypeAdapter(Annotated[list[_PullRequest], Field(max_length=50)])


class _Object(_Model):
    sha: _SHA


class _CommitData(_Model):
    message: str = Field(max_length=4096)
    tree: _Object


class _Commit(_Object):
    commit: _CommitData
    parents: list[_Object] = Field(max_length=2)


class _BranchCommit(_Model):
    id: _SHA


class _Branch(_Model):
    name: str
    commit: _BranchCommit
    protected: bool
    effective_branch_protection_name: str = ""


class _Entry(_Object):
    path: str
    mode: Literal["100644", "100755", "040000"]
    type: Literal["blob", "tree"]
    size: _Size


class _Tree(_Object):
    tree: list[_Entry] | None = Field(max_length=MAX_TREE_ENTRIES)
    truncated: bool
    page: int = Field(default=1, ge=1, le=20)
    total_count: int = Field(ge=0, le=MAX_TREE_ENTRIES)


class _Blob(_Object):
    size: _Size
    encoding: Literal["base64"]
    content: str = Field(max_length=1_500_000)


def _name(value: str) -> None:
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value) is None:
        raise StorageValidationError


def _marker(value: str) -> None:
    if (
        not 1 <= len(value.encode("utf-8")) <= 512
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise StorageValidationError


def _sha(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise StorageValidationError


def _branch(value: str) -> None:
    # A deliberately narrower subset of Git ref names, including retained refs.
    if (
        not 1 <= len(value) <= 100
        or re.fullmatch(r"[A-Za-z0-9_/-]+", value) is None
        or any(not part or part.startswith("-") for part in value.split("/"))
        or re.fullmatch(r"[0-9a-fA-F]+", value) is not None
    ):
        raise StorageValidationError


def _blob_sha(content: bytes) -> str:
    # Git SHA-1 is an object identifier, not a security primitive.
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\x00" + content, usedforsecurity=False).hexdigest()


def _tree_sha(entries: dict[str, tuple[str, str]]) -> str:
    """Reconstruct the exact canonical Git tree, including executable modes."""
    directories: dict[str, dict[str, tuple[str, str]]] = {"": {}}
    for path, entry in entries.items():
        parent, _, name = path.rpartition("/")
        directories.setdefault(parent, {})[name] = entry
        parts = parent.split("/")
        for index in range(1, len(parts)):
            directories.setdefault("/".join(parts[:index]), {})
    root_sha = ""
    for path in sorted(directories, key=lambda value: (value.count("/") + bool(value)), reverse=True):
        children = directories[path]
        # Git sorts directory names as though they had a trailing slash.
        names = sorted(children, key=lambda name: (name + ("/" if children[name][1] == "40000" else "")).encode())
        content = b"".join(
            children[name][1].encode() + b" " + name.encode() + b"\x00" + bytes.fromhex(children[name][0])
            for name in names
        )
        root_sha = hashlib.sha1(
            b"tree " + str(len(content)).encode() + b"\x00" + content, usedforsecurity=False
        ).hexdigest()
        if path:
            parent, _, name = path.rpartition("/")
            directories[parent][name] = (root_sha, "40000")
    return root_sha


def _changed_entries(
    original: tuple[_Entry, ...], path: str, content: bytes | None, expected_blob_sha: str | None
) -> dict[str, tuple[str, str]]:
    before: dict[str, tuple[str, str]] = {entry.path: (entry.sha, entry.mode) for entry in original}
    if before.get(path, (None, ""))[0] != expected_blob_sha:
        raise StorageConflictError
    after = before.copy()
    if content is None:
        del after[path]
    else:
        after[path] = (_blob_sha(content), before.get(path, ("", "100644"))[1])
    total = sum(entry.size for entry in original if entry.path != path) + (len(content) if content else 0)
    if len(after) > MAX_FILES or total > MAX_TEXT_BYTES:
        raise StorageValidationError
    if any("/".join(item.split("/")[:index]) in after for item in after for index in range(1, len(item.split("/")))):
        raise StorageValidationError
    directories = {"/".join(item.split("/")[:index]) for item in after for index in range(1, len(item.split("/")))}
    if len(after) + len(directories) > MAX_TREE_ENTRIES:
        raise StorageValidationError
    return after


def _protection(branch: str) -> dict[str, object]:
    return {
        "branch_name": branch,
        "rule_name": branch,
        "enable_push": False,
        "enable_push_whitelist": False,
        "push_whitelist_deploy_keys": False,
        "enable_force_push": False,
        "enable_force_push_allowlist": False,
        "force_push_allowlist_deploy_keys": False,
        "enable_merge_whitelist": True,
        "merge_whitelist_usernames": [],
        "merge_whitelist_teams": [],
        "block_admin_merge_override": True,
        "enable_bypass_allowlist": False,
        "bypass_allowlist_usernames": [],
        "bypass_allowlist_teams": [],
        "unprotected_file_patterns": "",
    }


def _matches_protection(payload: object, expected: dict[str, object]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key, value in expected.items():
        # rule_name is authoritative; branch_name is a deprecated API alias.
        if key == "branch_name":
            continue
        if key not in payload:
            return False
        observed = payload[key]
        # Gitea may encode an empty Go slice as null rather than [].
        if isinstance(value, list) and not value and observed is None:
            continue
        if type(observed) is not type(value) or observed != value:
            return False
    return True


def _sanitized[**P, T](
    operation: Callable[P, Coroutine[object, object, T]],
) -> Callable[P, Coroutine[object, object, T]]:
    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        error: StorageError
        try:
            return await operation(*args, **kwargs)
        except StorageValidationError:
            error = StorageValidationError()
        except StorageConflictError:
            error = StorageConflictError()
        except (StorageError, ValueError, TypeError, KeyError, OSError, TimeoutError, RuntimeError):
            error = StorageError()
        # Outside the handler and any async-generator exception context.
        raise error

    return run


class GiteaGitStorage:
    """Service-owned private repositories; no forks, grants or caller URLs.

    Operations are serialized within this adapter and have one wall deadline,
    including admission, body reads and recovery. Gitea's contents API has no
    expected-HEAD CAS: the application must additionally serialize writers across
    backend processes. Native collaborators must remain read-only. Parent and
    complete resulting state are checked even after a successful HTTP response.
    """

    def __init__(self, transport: GiteaTransport) -> None:
        self._transport = transport
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        async with asyncio.timeout(self._transport.operation_timeout_seconds), self._lock:
            yield

    @_sanitized
    async def identity(self) -> StorageIdentity:
        async with self._operation():
            return await self._transport.current_user()

    @overload
    async def _get(self, path: str, *, missing: Literal[False] = False, large: bool = False) -> bytes: ...

    @overload
    async def _get(self, path: str, *, missing: bool, large: bool = False) -> bytes | None: ...

    async def _get(self, path: str, *, missing: bool = False, large: bool = False) -> bytes | None:
        status, body = await self._transport.request(
            "GET", "/api/v1" + path, max_response_bytes=2_097_152 if large else 65_536
        )
        if missing and status == 404:
            return None
        if status != 200:
            raise StorageError
        return body

    async def _mutate(self, method: str, path: str, payload: dict[str, object]) -> None:
        """Exactly one attempt; read-back, not an HTTP success, decides completion."""
        # May have committed. The enclosing operation must inspect exact state.
        with suppress(StorageError):
            await self._transport.request(
                method,
                "/api/v1" + path,
                content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(),
                max_response_bytes=2_097_152,
            )

    def _path(self, identity: StorageIdentity, name: str) -> str:
        _name(name)
        if not identity.login or len(identity.login) > 255 or "/" in identity.login:
            raise StorageError
        return "/repos/" + quote(identity.login, safe="") + "/" + name

    async def _repository(
        self, identity: StorageIdentity, name: str, *, marker: str | None = None, missing: bool = False
    ) -> _Repository | None:
        body = await self._get(self._path(identity, name), missing=missing)
        if body is None:
            return None
        repository = _Repository.model_validate_json(body)
        if (
            repository.owner.id != identity.id
            or repository.owner.login != identity.login
            or repository.name != name
            or not repository.description
            or (marker is not None and repository.description != marker)
        ):
            raise StorageConflictError
        return repository

    async def _commit(self, path: str, sha: str) -> _Commit:
        _sha(sha)
        commit = _Commit.model_validate_json(await self._get(path + "/git/commits/" + sha))
        if commit.sha != sha:
            raise StorageError
        return commit

    async def _resolve(self, path: str, ref: str) -> _Commit:
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            return await self._commit(path, ref)
        _branch(ref)
        branch = await self._get_branch(path, ref)
        if branch is None:
            raise StorageConflictError
        return await self._commit(path, branch.commit.id)

    async def _get_branch(self, path: str, branch: str) -> _Branch | None:
        body = await self._get(path + "/branches/" + quote(branch, safe=""), missing=True)
        if body is None:
            return None
        result = _Branch.model_validate_json(body)
        if result.name != branch:
            raise StorageError
        return result

    def _snapshot(self, repository: _Repository, commit: _Commit) -> GitSnapshot:
        return GitSnapshot(
            repository_id=repository.id,
            owner_id=repository.owner.id,
            owner=repository.owner.login,
            name=repository.name,
            commit_sha=commit.sha,
            tree_sha=commit.commit.tree.sha,
        )

    async def _entries(self, path: str, commit: _Commit) -> tuple[_Entry, ...]:
        paths: set[str] = set()
        entries: list[_Entry] = []
        total = 0
        for entry in await self._tree_entries(path, commit.commit.tree.sha):
            validate_path(entry.path)
            if entry.path in paths:
                raise StorageError
            paths.add(entry.path)
            if entry.type == "tree" and entry.mode == "040000":
                continue
            if entry.type != "blob" or entry.mode not in {"100644", "100755"}:
                raise StorageError
            entries.append(entry)
            total += entry.size
            if len(entries) > MAX_FILES or total > MAX_TEXT_BYTES:
                raise StorageError
        if _tree_sha({entry.path: (entry.sha, entry.mode) for entry in entries}) != commit.commit.tree.sha:
            raise StorageError
        return tuple(sorted(entries, key=lambda entry: entry.path))

    async def _tree_entries(self, path: str, tree_sha: str) -> tuple[_Entry, ...]:
        result: list[_Entry] = []
        count: int | None = None
        for page in range(1, 21):
            tree = _Tree.model_validate_json(
                await self._get(
                    path + "/git/trees/" + tree_sha + f"?recursive=true&per_page=1000&page={page}", large=True
                )
            )
            if tree.sha != tree_sha or tree.page != page or (count is not None and tree.total_count != count):
                raise StorageError
            count = tree.total_count
            result.extend(tree.tree or ())
            if len(result) > count or (tree.truncated and not tree.tree):
                raise StorageError
            if not tree.truncated:
                if len(result) != count:
                    raise StorageError
                return tuple(result)
        raise StorageError

    async def _files(self, path: str, commit: _Commit) -> tuple[GitFile, ...]:
        result: list[GitFile] = []
        for entry in await self._entries(path, commit):
            blob = _Blob.model_validate_json(await self._get(path + "/git/blobs/" + entry.sha, large=True))
            content = base64.b64decode(blob.content.replace("\n", ""), validate=True)
            if blob.sha != entry.sha or blob.size != entry.size or len(content) != entry.size:
                raise StorageError
            if _blob_sha(content) != entry.sha:
                raise StorageError
            validate_files((TextFile(entry.path, content),))
            result.append(GitFile(entry.path, entry.sha, entry.size, content))
        return tuple(result)

    @_sanitized
    async def ensure_snapshot(
        self, name: str, marker: str, files: tuple[TextFile, ...], *, branch: str = "main"
    ) -> GitSnapshot:
        async with self._operation():
            _name(name)
            _marker(marker)
            _branch(branch)
            validate_files(files)
            identity = await self._transport.current_user()
            path = self._path(identity, name)
            repository = await self._repository(identity, name, marker=marker, missing=True)
            if repository is None:
                await self._mutate(
                    "POST",
                    "/user/repos",
                    {
                        "name": name,
                        "description": marker,
                        "private": True,
                        "auto_init": False,
                        "default_branch": branch,
                    },
                )
                repository = await self._repository(identity, name, marker=marker)
            if repository is None:
                raise StorageError
            if repository.empty:
                # One initial commit; never expose partially populated initial state.
                await self._mutate(
                    "POST",
                    path + "/contents",
                    {
                        "branch": branch,
                        "message": marker,
                        "files": [
                            {
                                "operation": "create",
                                "path": file.path,
                                "content": base64.b64encode(file.content).decode(),
                            }
                            for file in files
                        ],
                    },
                )
            commit = await self._resolve(path, branch)
            observed = await self._files(path, commit)
            if (
                commit.parents
                or commit.commit.message.rstrip("\n") != marker
                or {file.path: file.content for file in observed} != {file.path: file.content for file in files}
                or any(entry.mode != "100644" for entry in await self._entries(path, commit))
            ):
                raise StorageConflictError
            return self._snapshot(repository, commit)

    @_sanitized
    async def snapshot(self, name: str, ref: str) -> GitSnapshot:
        async with self._operation():
            identity = await self._transport.current_user()
            repository = await self._repository(identity, name)
            if repository is None:
                raise StorageError
            path = self._path(identity, name)
            commit = await self._resolve(path, ref)
            await self._entries(path, commit)
            return self._snapshot(repository, commit)

    @_sanitized
    async def files(self, name: str, ref: str) -> tuple[GitFile, ...]:
        async with self._operation():
            identity = await self._transport.current_user()
            await self._repository(identity, name)
            path = self._path(identity, name)
            return await self._files(path, await self._resolve(path, ref))

    @_sanitized
    async def retain(self, name: str, commit_sha: str, branch: str) -> None:
        async with self._operation():
            _sha(commit_sha)
            _branch(branch)
            identity = await self._transport.current_user()
            await self._repository(identity, name)
            path = self._path(identity, name)
            await self._entries(path, await self._commit(path, commit_sha))
            observed = await self._get_branch(path, branch)
            if observed is None:
                await self._mutate("POST", path + "/branches", {"new_branch_name": branch, "old_ref_name": commit_sha})
                observed = await self._get_branch(path, branch)
            if observed is None:
                raise StorageError
            if observed.commit.id != commit_sha:
                raise StorageConflictError
            rule_path = path + "/branch_protections/" + quote(branch, safe="")
            rule = await self._get(rule_path, missing=True)
            protection = _protection(branch)
            if rule is None:
                await self._mutate("POST", path + "/branch_protections", protection)
                rule = await self._get(rule_path)
            if rule is None:
                raise StorageError
            payload: object = json.loads(rule)
            if not _matches_protection(payload, protection):
                raise StorageConflictError
            observed = await self._get_branch(path, branch)
            if (
                observed is None
                or observed.commit.id != commit_sha
                or not observed.protected
                or observed.effective_branch_protection_name != branch
            ):
                raise StorageConflictError

    async def _retained_branch(self, path: str, branch: str, sha: str) -> None:
        observed = await self._get_branch(path, branch)
        if (
            observed is None
            or observed.commit.id != sha
            or not observed.protected
            or observed.effective_branch_protection_name != branch
        ):
            raise StorageConflictError
        rule = await self._get(path + "/branch_protections/" + quote(branch, safe=""), missing=True)
        if rule is None or not _matches_protection(json.loads(rule), _protection(branch)):
            raise StorageConflictError

    async def _find_pull_request(self, path: str, base: str, head: str, marker: str, title: str) -> _PullRequest | None:
        found: _PullRequest | None = None
        seen: set[int] = set()
        # Scan every state: a closed/merged or foreign PR is a conflict, not an
        # excuse to POST again. Incomplete/oversized/repeating pages fail closed.
        for page in range(1, 21):
            pulls = _PULL_PAGE.validate_json(
                await self._get(path + f"/pulls?state=all&sort=oldest&limit=50&page={page}", large=True)
            )
            for pull in pulls:
                if pull.number in seen:
                    raise StorageError
                seen.add(pull.number)
                if (pull.base.ref == base and pull.head.ref == head) or pull.body == marker or pull.title == title:
                    if found is not None:
                        raise StorageConflictError
                    found = pull
            if not pulls:
                if found is None:
                    return None
                detail = _PullRequest.model_validate_json(await self._get(path + f"/pulls/{found.number}", large=True))
                if detail != found:
                    raise StorageConflictError
                return detail
        raise StorageError

    def _check_pull_request(
        self,
        pull: _PullRequest,
        repository: _Repository,
        identity: StorageIdentity,
        payload: dict[str, object],
        base_sha: str,
        head_sha: str,
    ) -> None:
        if (
            pull.user.id != identity.id
            or pull.user.login != identity.login
            or pull.title != payload["title"]
            or pull.body != payload["body"]
            or pull.state != "open"
            or pull.merged
        ):
            raise StorageConflictError
        for ref, branch, sha in ((pull.base, payload["base"], base_sha), (pull.head, payload["head"], head_sha)):
            if (
                ref.ref != branch
                or ref.sha != sha
                or ref.repo_id != repository.id
                or ref.repo.id != repository.id
                or ref.repo.owner != repository.owner
                or ref.repo.name != repository.name
                or not ref.repo.private
                or ref.repo.fork
            ):
                raise StorageConflictError

    @_sanitized
    async def ensure_pull_request(
        self,
        name: str,
        *,
        base_branch: str,
        base_sha: str,
        head_branch: str,
        head_sha: str,
        marker: str,
    ) -> GitPullRequest:
        """Ensure one open, service-authored same-repository immutable comparison.

        The caller persists intent and fences competing operations before I/O.
        No refs, protections, work branches or existing PRs are ever changed here.
        """
        async with self._operation():
            _name(name)
            _branch(base_branch)
            _branch(head_branch)
            _sha(base_sha)
            _sha(head_sha)
            _marker(marker)
            if base_branch == head_branch or base_sha == head_sha:
                raise StorageConflictError
            identity = await self._transport.current_user()
            repository = await self._repository(identity, name)
            if repository is None:
                raise StorageError
            path = self._path(identity, name)
            await self._retained_branch(path, base_branch, base_sha)
            await self._retained_branch(path, head_branch, head_sha)
            title = "prodguild:" + hashlib.sha256(marker.encode()).hexdigest()
            payload: dict[str, object] = {"base": base_branch, "head": head_branch, "title": title, "body": marker}
            pull = await self._find_pull_request(path, base_branch, head_branch, marker, title)
            if pull is None:
                status: int | None = None
                body: bytes | None = None
                # Exactly one attempt. Never trust POST success without read-back.
                with suppress(StorageError):
                    status, body = await self._transport.request(
                        "POST",
                        path="/api/v1" + path + "/pulls",
                        content=json.dumps(payload).encode(),
                        max_response_bytes=2_097_152,
                    )
                created: _PullRequest | None = None
                if status in {200, 201} and body is not None:
                    try:
                        created = _PullRequest.model_validate_json(body)
                    except ValueError:
                        raise StorageConflictError from None
                    self._check_pull_request(created, repository, identity, payload, base_sha, head_sha)
                pull = await self._find_pull_request(path, base_branch, head_branch, marker, title)
                if pull is None:
                    if status in {409, 422}:
                        raise StorageConflictError
                    raise StorageError
                if created is not None and created != pull:
                    raise StorageConflictError
            self._check_pull_request(pull, repository, identity, payload, base_sha, head_sha)
            await self._retained_branch(path, base_branch, base_sha)
            await self._retained_branch(path, head_branch, head_sha)
            return GitPullRequest(repository.id, repository.owner.id, pull.number, pull.id, base_sha, head_sha)

    @_sanitized
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
        async with self._operation():
            _branch(branch)
            _sha(expected_head_sha)
            _marker(marker)
            validate_path(path)
            if expected_blob_sha is not None:
                _sha(expected_blob_sha)
            if content is not None:
                validate_files((TextFile(path, content),))
            elif expected_blob_sha is None:
                raise StorageValidationError
            identity = await self._transport.current_user()
            repository = await self._repository(identity, name)
            if repository is None:
                raise StorageError
            repo_path = self._path(identity, name)
            parent = await self._commit(repo_path, expected_head_sha)
            original = await self._entries(repo_path, parent)
            after = _changed_entries(original, path, content, expected_blob_sha)
            head = await self._resolve(repo_path, branch)
            if head.sha == expected_head_sha:
                if head.commit.message.rstrip("\n") == marker:
                    raise StorageConflictError
                operation = "delete" if content is None else "create" if expected_blob_sha is None else "update"
                await self._mutate(
                    "POST",
                    repo_path + "/contents",
                    {
                        "branch": branch,
                        "message": marker,
                        "files": [
                            {
                                "operation": operation,
                                "path": path,
                                "sha": expected_blob_sha or "",
                                "content": base64.b64encode(content).decode() if content is not None else "",
                            }
                        ],
                    },
                )
                head = await self._resolve(repo_path, branch)
            if (
                [item.sha for item in head.parents] != [expected_head_sha]
                or head.commit.message.rstrip("\n") != marker
                or {entry.path: (entry.sha, entry.mode) for entry in await self._entries(repo_path, head)} != after
            ):
                raise StorageConflictError
            # Read actual bytes as well as verifying Git blob IDs and whole tree.
            await self._files(repo_path, head)
            return self._snapshot(repository, head)
