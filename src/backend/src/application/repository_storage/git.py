from dataclasses import dataclass
from typing import Protocol, override

from src.application.repository_storage.ports import StorageError, StorageIdentity

MAX_FILES = 100
MAX_TEXT_BYTES = 1_048_576
MAX_PATH_BYTES = 1024
MAX_TREE_ENTRIES = 1000


class StorageConflictError(StorageError):
    @override
    def __str__(self) -> str:
        return "Repository storage version conflict"


class StorageValidationError(StorageError):
    @override
    def __str__(self) -> str:
        return "Invalid repository storage input"


@dataclass(frozen=True, slots=True)
class TextFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    repository_id: int
    owner_id: int
    owner: str
    name: str
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True, slots=True)
class GitPullRequest:
    repository_id: int
    owner_id: int
    number: int
    external_id: int
    base_sha: str
    head_sha: str


@dataclass(frozen=True, slots=True)
class GitFile:
    path: str
    sha: str
    size: int
    content: bytes


def validate_path(path: str) -> None:
    """Validate literal POSIX paths, without normalizing or percent decoding."""
    try:
        valid = (
            0 < len(path.encode("utf-8")) <= MAX_PATH_BYTES
            and "\\" not in path
            and not any(ord(character) < 32 or ord(character) == 127 for character in path)
            and all(part and part not in {".", ".."} and part.casefold() != ".git" for part in path.split("/"))
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise StorageValidationError


def validate_files(files: tuple[TextFile, ...]) -> None:
    """Bound a complete nonempty UTF-8 text snapshot, including path collisions."""
    if not 1 <= len(files) <= MAX_FILES:
        raise StorageValidationError
    paths: set[str] = set()
    total = 0
    for file in files:
        validate_path(file.path)
        total += len(file.content)
        if total > MAX_TEXT_BYTES or b"\x00" in file.content or file.path in paths:
            raise StorageValidationError
        try:
            file.content.decode("utf-8")
        except UnicodeError:
            break
        paths.add(file.path)
    else:
        directories = {"/".join(path.split("/")[:index]) for path in paths for index in range(1, len(path.split("/")))}
        if not directories & paths and len(directories) + len(paths) <= MAX_TREE_ENTRIES:
            return
    raise StorageValidationError


class GitStorage(Protocol):
    async def identity(self) -> StorageIdentity: ...

    async def ensure_snapshot(
        self, name: str, marker: str, files: tuple[TextFile, ...], *, branch: str = "main"
    ) -> GitSnapshot: ...

    async def snapshot(self, name: str, ref: str) -> GitSnapshot: ...

    async def files(self, name: str, ref: str) -> tuple[GitFile, ...]: ...

    async def retain(self, name: str, commit_sha: str, branch: str) -> None: ...

    async def ensure_pull_request(
        self,
        name: str,
        *,
        base_branch: str,
        base_sha: str,
        head_branch: str,
        head_sha: str,
        marker: str,
    ) -> GitPullRequest: ...

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
    ) -> GitSnapshot: ...
