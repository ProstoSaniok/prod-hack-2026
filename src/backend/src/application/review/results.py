from typing import Protocol
from uuid import UUID

from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.user.entity import UserId


class ReviewCompletionStore(Protocol):
    async def complete(self, actor: UserId, assignment_id: UUID, draft_version: int, key: str) -> CachedResponse: ...


class ReviewResultsReader(Protocol):
    async def reviews(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def review(self, actor: UserId, review_id: UUID) -> bytes: ...
    async def feedback(self, actor: UserId, submission_id: UUID) -> bytes: ...
    async def progression(self, actor: UserId, submission_id: UUID) -> bytes: ...


class ReviewResultsQueries:
    def __init__(self, identity: IdentityProvider, reader: ReviewResultsReader) -> None:
        self.identity, self.reader = identity, reader

    async def reviews(self, *, limit: int, offset: int) -> bytes:
        return await self.reader.reviews((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def review(self, review_id: UUID) -> bytes:
        return await self.reader.review((await self.identity.get_current_user()).id, review_id)

    async def feedback(self, submission_id: UUID) -> bytes:
        return await self.reader.feedback((await self.identity.get_current_user()).id, submission_id)

    async def progression(self, submission_id: UUID) -> bytes:
        return await self.reader.progression((await self.identity.get_current_user()).id, submission_id)
