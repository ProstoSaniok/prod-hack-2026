"""State-changing use cases; queries and persistence remain separate dependencies."""

from uuid import UUID

from src.application.common.identity_provider import IdentityProvider
from src.application.common.interactor import to_interactor
from src.application.development.workflow import ApplicationInput, AssessmentInput, DevelopmentStore
from src.application.operations.idempotency import CachedResponse


@to_interactor
class ApplyReviewerApplication:
    identity: IdentityProvider
    store: DevelopmentStore

    async def execute(self, command: ApplicationInput, key: str) -> CachedResponse:
        return await self.store.apply((await self.identity.get_current_user()).id, command, key)


@to_interactor
class AssessReviewerApplication:
    identity: IdentityProvider
    store: DevelopmentStore

    async def execute(self, application_id: UUID, command: AssessmentInput, key: str) -> CachedResponse:
        return await self.store.assess((await self.identity.get_current_user()).id, application_id, command, key)
