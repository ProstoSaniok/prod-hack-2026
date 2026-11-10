"""State-changing use cases; queries and persistence remain separate dependencies."""

from uuid import UUID

from src.application.catalog.management import CaseValidationError
from src.application.common.identity_provider import IdentityProvider
from src.application.common.interactor import to_interactor
from src.application.operations.idempotency import CachedResponse
from src.application.project.snapshot_reading import (
    PinnedSource,
    snapshot_range_exists,
)
from src.application.repository_storage.git import GitStorage, validate_path
from src.application.review.entity import DiffSide
from src.application.review.priority import PriorityStore
from src.application.review.quality import QualityInput, ReviewQualityStore
from src.application.review.results import ReviewCompletionStore
from src.application.review.workspace import DraftComment, DraftContent, ReviewStore


@to_interactor
class SetReviewMode:
    identity: IdentityProvider
    store: ReviewStore

    async def execute(self, enabled: bool, key: str) -> CachedResponse:  # noqa: FBT001
        return await self.store.set_mode((await self.identity.get_current_user()).id, enabled, key)


@to_interactor
class ClaimReviewAssignment:
    identity: IdentityProvider
    store: ReviewStore

    async def execute(self, key: str) -> CachedResponse:
        return await self.store.claim((await self.identity.get_current_user()).id, key)


@to_interactor
class CancelReviewAssignment:
    identity: IdentityProvider
    store: ReviewStore

    async def execute(self, assignment_id: UUID, key: str) -> CachedResponse:
        return await self.store.cancel((await self.identity.get_current_user()).id, assignment_id, key)


@to_interactor
class SaveReviewDraft:
    identity: IdentityProvider
    store: ReviewStore
    storage: GitStorage

    async def execute(self, assignment_id: UUID, content: DraftContent, key: str) -> CachedResponse:
        actor = (await self.identity.get_current_user()).id
        replay = await self.store.replay_draft(actor, assignment_id, content, key)
        if replay is not None:
            return replay
        source = await self.store.source(actor, assignment_id)
        await self._validate_comments(source, content.comments)
        return await self.store.save_draft(actor, assignment_id, content, key)

    async def _validate_comments(self, source: PinnedSource, comments: tuple[DraftComment, ...]) -> None:
        for comment in comments:
            if comment.path is None:
                if comment.line is not None or comment.side is not None or comment.end_line is not None:
                    raise CaseValidationError("General comments cannot contain a line or side")
            else:
                validate_path(comment.path)
                if (
                    type(comment.line) is not int
                    or comment.line < 1
                    or comment.side not in {DiffSide.OLD, DiffSide.NEW}
                    or (
                        comment.end_line is not None
                        and (type(comment.end_line) is not int or comment.end_line < comment.line)
                    )
                ):
                    raise CaseValidationError("File comments require a positive line and old/new side")
        inline = [comment for comment in comments if comment.path is not None]
        if not inline:
            return
        before = {file.path: file for file in await self.storage.files(source.name, source.base_sha)}
        after = {file.path: file for file in await self.storage.files(source.name, source.head_sha)}
        for comment in inline:
            if comment.path is None or comment.line is None:
                raise CaseValidationError("Invalid file comment")
            if not snapshot_range_exists(
                (before if comment.side == DiffSide.OLD else after).get(comment.path),
                start=comment.line,
                end=comment.end_line if comment.end_line is not None else comment.line,
            ):
                raise CaseValidationError("Comment range is absent from the pinned snapshot")


@to_interactor
class CompleteReview:
    identity: IdentityProvider
    completion: ReviewCompletionStore

    async def execute(self, assignment_id: UUID, draft_version: int, key: str) -> CachedResponse:
        return await self.completion.complete(
            (await self.identity.get_current_user()).id, assignment_id, draft_version, key
        )


@to_interactor
class AssessReviewQuality:
    identity: IdentityProvider
    store: ReviewQualityStore

    async def execute(self, review_id: UUID, command: QualityInput, key: str) -> CachedResponse:
        return await self.store.assess((await self.identity.get_current_user()).id, review_id, command, key)


@to_interactor
class ReviseReview:
    identity: IdentityProvider
    store: ReviewQualityStore

    async def execute(
        self, review_id: UUID, expected_revision_id: UUID, expected_draft_version: int, key: str
    ) -> CachedResponse:
        actor = (await self.identity.get_current_user()).id
        if type(expected_draft_version) is not int or expected_draft_version < 1:
            raise CaseValidationError("Expected draft version must be a positive integer")
        return await self.store.revise(actor, review_id, expected_revision_id, expected_draft_version, key)


@to_interactor
class PurchaseReviewPriority:
    identity: IdentityProvider
    store: PriorityStore

    async def execute(self, submission_id: UUID, key: str) -> CachedResponse:
        return await self.store.purchase((await self.identity.get_current_user()).id, submission_id, key)


@to_interactor
class CancelReviewPriority:
    identity: IdentityProvider
    store: PriorityStore

    async def execute(self, request_id: UUID, key: str) -> CachedResponse:
        return await self.store.cancel((await self.identity.get_current_user()).id, request_id, key)
