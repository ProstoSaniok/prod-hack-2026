"""Owner priority purchases and persisted eligible scheduling policy."""

from typing import Protocol
from uuid import UUID

from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.user.entity import UserId

PRIORITY_POLICY_VERSION = "prodguild-priority-v1"


class PriorityStore(Protocol):
    async def purchase(self, actor: UserId, submission_id: UUID, key: str) -> CachedResponse: ...
    async def cancel(self, actor: UserId, request_id: UUID, key: str) -> CachedResponse: ...
    async def get(self, actor: UserId, request_id: UUID) -> bytes: ...
    async def list(self, actor: UserId, limit: int, offset: int) -> bytes: ...


class PriorityQueries:
    def __init__(self, identity: IdentityProvider, store: PriorityStore) -> None:
        self.identity, self.store = identity, store

    async def get(self, request_id: UUID) -> bytes:
        return await self.store.get((await self.identity.get_current_user()).id, request_id)

    async def list(self, limit: int, offset: int) -> bytes:
        return await self.store.list((await self.identity.get_current_user()).id, limit, offset)
