import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

from src.application.catalog.management import CaseNotFoundError
from src.application.common.errors import ApplicationError
from src.application.repository_storage.git import GitFile, GitStorage, validate_path

type SubmissionSide = Literal["base", "head"]


@dataclass(frozen=True, kw_only=True)
class PinnedSource:
    submission_id: UUID
    name: str
    base_sha: str
    head_sha: str


class SnapshotTooLargeError(ApplicationError):
    pass


class SnapshotReads:
    def __init__(self, storage: GitStorage) -> None:
        self.storage = storage

    async def files(
        self,
        source: PinnedSource,
        *,
        side: SubmissionSide = "head",
        path: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> bytes:
        if path is not None:
            validate_path(path)
        commit = source.base_sha if side == "base" else source.head_sha
        files = sorted(await self.storage.files(source.name, commit), key=lambda file: file.path)
        if path is not None:
            file = next((file for file in files if file.path == path), None)
            if file is None:
                raise CaseNotFoundError
            return _encode(_file(file, commit))
        entries = [
            {
                "path": file.path,
                "type": "file",
                "sha": file.sha,
                "size": file.size,
                "binary": False,
                "download_url": None,
            }
            for file in files[offset : offset + limit]
        ]
        return _encode({
            "submission_id": str(source.submission_id),
            "commit_sha": commit,
            "side": side,
            "entries": entries,
            "offset": offset,
            "next_offset": offset + len(entries) if offset + len(entries) < len(files) else None,
        })

    async def snapshot(self, source: PinnedSource) -> bytes:
        files = await self.storage.files(source.name, source.head_sha)
        return _encode({
            "submission_id": str(source.submission_id),
            "commit_sha": source.head_sha,
            "files": [_file(file, source.head_sha) for file in sorted(files, key=lambda file: file.path)],
            "text_bytes": sum(file.size for file in files),
        })

    async def diff(self, source: PinnedSource, limit: int, offset: int, path: str | None = None) -> bytes:
        if path is not None:
            validate_path(path)
        before = {file.path: file for file in await self.storage.files(source.name, source.base_sha)}
        after = {file.path: file for file in await self.storage.files(source.name, source.head_sha)}
        changed = sorted(
            key
            for key in before.keys() | after.keys()
            if key not in before or key not in after or before[key].sha != after[key].sha
        )
        fields: dict[str, object] = {
            "submission_id": str(source.submission_id),
            "base_commit_sha": source.base_sha,
            "head_commit_sha": source.head_sha,
            "offset": offset,
        }
        if path is not None:
            if path not in changed:
                raise CaseNotFoundError
            return _encode({**fields, "path": path, **_line_page(before.get(path), after.get(path), limit, offset)})
        entries = []
        for key in changed[offset : offset + limit]:
            old, new = before.get(key), after.get(key)
            additions, deletions = _counts(old, new)
            entries.append({
                "old_path": key if old else None,
                "new_path": key if new else None,
                "status": "added" if old is None else "deleted" if new is None else "modified",
                "binary": False,
                "additions": additions,
                "deletions": deletions,
                "comment_path": key,
            })
        return _encode({
            **fields,
            "entries": entries,
            "next_offset": offset + len(entries) if offset + len(entries) < len(changed) else None,
        })


def _encode(value: object) -> bytes:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > 2 * 1024 * 1024:
        raise SnapshotTooLargeError
    return body


def _file(file: GitFile, commit: str) -> dict[str, object]:
    return {
        "path": file.path,
        "type": "file",
        "sha": file.sha,
        "size": file.size,
        "commit_sha": commit,
        "content": file.content.decode("utf-8"),
        "binary": False,
        "download_url": None,
    }


def _git_lines(file: GitFile | None) -> list[str]:
    if file is None:
        return []
    lines = file.content.decode("utf-8").split("\n")
    return [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


def _changes(
    old: GitFile | None, new: GitFile | None
) -> tuple[list[str], list[str], Sequence[tuple[str, int, int, int, int]]]:
    before, after = _git_lines(old), _git_lines(new)
    if len(before) * len(after) > 250_000:
        return before, after, [("replace", 0, len(before), 0, len(after))]
    return before, after, SequenceMatcher(None, before, after, autojunk=False).get_opcodes()


def _counts(old: GitFile | None, new: GitFile | None) -> tuple[int, int]:
    _before, _after, changes = _changes(old, new)
    return (
        sum(end - start for tag, _, _, start, end in changes if tag != "equal"),
        sum(end - start for tag, start, end, _, _ in changes if tag != "equal"),
    )


def diff_position_exists(old: GitFile | None, new: GitFile | None, *, side: SubmissionSide, line: int) -> bool:
    if old is None and new is None or old is not None and new is not None and old.sha == new.sha:
        return False
    return any(
        (old_line if side == "base" else new_line) == line
        for _kind, old_line, new_line, _text in _coordinates(old, new)
    )


def snapshot_range_exists(file: GitFile | None, *, start: int, end: int) -> bool:
    """Coordinates are inclusive LF lines in one retained file, including unchanged files."""
    return file is not None and 1 <= start <= end <= len(_git_lines(file))


def _line_page(old: GitFile | None, new: GitFile | None, limit: int, offset: int) -> dict[str, object]:
    lines: list[dict[str, object]] = []
    text_bytes = 0
    for position, (kind, old_line, new_line, text) in enumerate(_coordinates(old, new)):
        if position < offset:
            continue
        raw = text.removesuffix("\n").encode("utf-8")
        bounded = raw[:65_536].decode("utf-8", errors="ignore")
        if len(lines) >= limit or text_bytes + len(bounded.encode()) > 262_144:
            return {"lines": lines, "next_offset": position}
        text_bytes += len(bounded.encode())
        lines.append({
            "kind": kind,
            "old_line": old_line,
            "new_line": new_line,
            "text": bounded,
            "text_truncated": len(raw) > 65_536,
        })
    return {"lines": lines, "next_offset": None}


def _coordinates(old: GitFile | None, new: GitFile | None) -> Iterator[tuple[str, int | None, int | None, str]]:
    before, after, changes = _changes(old, new)
    for tag, old_start, old_end, new_start, new_end in changes:
        if tag == "equal":
            for index in range(old_start, old_end):
                yield "context", index + 1, new_start + index - old_start + 1, before[index]
        else:
            for index in range(old_start, old_end):
                yield "deletion", index + 1, None, before[index]
            for index in range(new_start, new_end):
                yield "addition", None, index + 1, after[index]
