from typing import Protocol
from uuid import UUID

from src.application.common.identity_provider import IdentityProvider
from src.application.common.interactor import to_interactor
from src.application.user.entity import UserId


class ReferenceReadinessStore(Protocol):
    async def set_ready(
        self,
        actor: UserId,
        case_id: UUID,
        expected_case_version: int,
        expected_version: int,
        expected_commit_sha: str,
        *,
        ready: bool,
        key: str,
    ) -> bytes: ...


@to_interactor
class SetReferenceReady:
    identity: IdentityProvider
    store: ReferenceReadinessStore

    async def execute(
        self,
        case_id: UUID,
        expected_case_version: int,
        expected_version: int,
        expected_commit_sha: str,
        *,
        ready: bool,
        key: str,
    ) -> bytes:
        return await self.store.set_ready(
            (await self.identity.get_current_user()).id,
            case_id,
            expected_case_version,
            expected_version,
            expected_commit_sha,
            ready=ready,
            key=key,
        )
