from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.project.snapshot_reading import (
    PinnedSource,
    SnapshotReads,
    SubmissionSide,
)
from src.application.repository_storage.git import GitStorage
from src.application.review.entity import DiffSide, ReviewVerdict
from src.application.user.entity import UserId


@dataclass(frozen=True, kw_only=True)
class DraftComment:
    body: str
    path: str | None
    line: int | None
    side: DiffSide | None
    end_line: int | None = None


@dataclass(frozen=True, kw_only=True)
class DraftContent:
    expected_version: int
    score: Decimal | None
    verdict: ReviewVerdict | None
    strengths: str
    problems: str
    next_steps: str
    comments: tuple[DraftComment, ...]


class ReviewStore(Protocol):
    async def mode(self, actor: UserId) -> bytes: ...
    async def set_mode(self, actor: UserId, enabled: bool, key: str) -> CachedResponse: ...  # noqa: FBT001
    async def claim(self, actor: UserId, key: str) -> CachedResponse: ...
    async def cancel(self, actor: UserId, assignment_id: UUID, key: str) -> CachedResponse: ...
    async def assignments(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def assignment(self, actor: UserId, assignment_id: UUID) -> bytes: ...
    async def workspace(self, actor: UserId, assignment_id: UUID) -> bytes: ...
    async def draft(self, actor: UserId, assignment_id: UUID) -> bytes: ...
    async def replay_draft(
        self, actor: UserId, assignment_id: UUID, content: DraftContent, key: str
    ) -> CachedResponse | None: ...
    async def save_draft(
        self, actor: UserId, assignment_id: UUID, content: DraftContent, key: str
    ) -> CachedResponse: ...
    async def source(self, actor: UserId, assignment_id: UUID) -> PinnedSource: ...


class ReviewQueries:
    def __init__(self, identity: IdentityProvider, store: ReviewStore, storage: GitStorage) -> None:
        self.identity, self.store, self.storage = identity, store, storage
        self.snapshots = SnapshotReads(storage)

    async def mode(self) -> bytes:
        return await self.store.mode((await self.identity.get_current_user()).id)

    async def assignments(self, *, limit: int, offset: int) -> bytes:
        return await self.store.assignments((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def assignment(self, assignment_id: UUID) -> bytes:
        return await self.store.assignment((await self.identity.get_current_user()).id, assignment_id)

    async def workspace(self, assignment_id: UUID) -> bytes:
        return await self.store.workspace((await self.identity.get_current_user()).id, assignment_id)

    async def draft(self, assignment_id: UUID) -> bytes:
        return await self.store.draft((await self.identity.get_current_user()).id, assignment_id)

    async def files(
        self,
        assignment_id: UUID,
        *,
        side: SubmissionSide = "head",
        path: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> bytes:
        source = await self.store.source((await self.identity.get_current_user()).id, assignment_id)
        return await self.snapshots.files(source, side=side, path=path, limit=limit, offset=offset)

    async def snapshot(self, assignment_id: UUID) -> bytes:
        source = await self.store.source((await self.identity.get_current_user()).id, assignment_id)
        return await self.snapshots.snapshot(source)

    async def diff(self, assignment_id: UUID, limit: int, offset: int, path: str | None = None) -> bytes:
        source = await self.store.source((await self.identity.get_current_user()).id, assignment_id)
        return await self.snapshots.diff(source, limit, offset, path)
