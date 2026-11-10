from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.catalog.management import CaseValidationError
from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.project.snapshot_reading import PinnedSource
from src.application.repository_storage.git import GitFile, GitStorage
from src.application.review.entity import DiffSide
from src.application.review.quality import ReviewQualityStore
from src.application.review.usecases import ReviseReview, SaveReviewDraft
from src.application.review.workspace import DraftComment, DraftContent, ReviewStore
from src.application.user.entity import User


def draft(*comments: DraftComment) -> DraftContent:
    return DraftContent(
        expected_version=1,
        score=None,
        verdict=None,
        strengths="",
        problems="",
        next_steps="",
        comments=comments,
    )


async def test_authorized_draft_replay_never_reads_git_or_writes_draft() -> None:
    user = User.factory()
    identity = AsyncMock(spec=IdentityProvider)
    identity.get_current_user.return_value = user
    store = AsyncMock(spec=ReviewStore)
    receipt = CachedResponse(status_code=200, body=b'{"version":2}')
    store.replay_draft.return_value = receipt
    storage = AsyncMock(spec=GitStorage)
    interactor = SaveReviewDraft(identity=identity, store=store, storage=storage)
    assignment = uuid4()
    content = draft()

    assert await interactor.execute(assignment, content, "replay") is receipt
    store.replay_draft.assert_awaited_once_with(user.id, assignment, content, "replay")
    store.source.assert_not_awaited()
    store.save_draft.assert_not_awaited()
    storage.files.assert_not_awaited()


@pytest.mark.parametrize(
    "comment",
    [
        DraftComment(body="general", path=None, line=1, side=None),
        DraftComment(body="inline", path="solution.py", line=0, side=None),
    ],
)
async def test_invalid_comment_stops_before_git_read_or_draft_mutation(comment: DraftComment) -> None:
    identity = AsyncMock(spec=IdentityProvider)
    identity.get_current_user.return_value = User.factory()
    store = AsyncMock(spec=ReviewStore)
    store.replay_draft.return_value = None
    store.source.return_value = PinnedSource(
        submission_id=uuid4(),
        name="private",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    storage = AsyncMock(spec=GitStorage)
    interactor = SaveReviewDraft(identity=identity, store=store, storage=storage)

    with pytest.raises(CaseValidationError):
        await interactor.execute(uuid4(), draft(comment), "save")
    store.save_draft.assert_not_awaited()
    storage.files.assert_not_awaited()


@pytest.mark.parametrize("version", [0, -1, True])
async def test_invalid_revision_version_cannot_reach_persistence(version: int) -> None:
    identity = AsyncMock(spec=IdentityProvider)
    identity.get_current_user.return_value = User.factory()
    store = AsyncMock(spec=ReviewQualityStore)
    interactor = ReviseReview(identity=cast(IdentityProvider, identity), store=cast(ReviewQualityStore, store))

    with pytest.raises(CaseValidationError):
        await interactor.execute(uuid4(), uuid4(), version, "revise")
    store.revise.assert_not_awaited()


@pytest.mark.parametrize("side", ["old", "new"])
async def test_range_comments_accept_unchanged_snapshot_and_keep_lf_coordinates(side: str) -> None:
    content = "first\r\v\u2028line\nsecond\nthird\n".encode()
    file = GitFile(path="unchanged.sql", sha="same", size=len(content), content=content)
    identity = AsyncMock(spec=IdentityProvider)
    identity.get_current_user.return_value = User.factory()
    store = AsyncMock(spec=ReviewStore)
    store.replay_draft.return_value = None
    store.source.return_value = PinnedSource(submission_id=uuid4(), name="private", base_sha="base", head_sha="head")
    storage = AsyncMock(spec=GitStorage)
    storage.files.return_value = (file,)
    interactor = SaveReviewDraft(identity=identity, store=store, storage=storage)
    command = draft(DraftComment(body="Three lines", path=file.path, line=1, end_line=3, side=DiffSide(side)))
    await interactor.execute(uuid4(), command, "range")
    store.save_draft.assert_awaited_once()
    assert storage.files.await_count == 2
    store.save_draft.reset_mock()
    with pytest.raises(CaseValidationError, match="absent"):
        await interactor.execute(
            uuid4(),
            draft(DraftComment(body="Out of bounds", path=file.path, line=1, end_line=4, side=DiffSide(side))),
            "outside",
        )
    store.save_draft.assert_not_awaited()


@pytest.mark.parametrize(("line", "end_line", "path"), [(2, 1, "a.sql"), (None, 2, None), (1, True, "a.sql")])
async def test_malformed_range_rejected_before_git(line: int | None, end_line: int, path: str | None) -> None:
    identity = AsyncMock(spec=IdentityProvider)
    identity.get_current_user.return_value = User.factory()
    store = AsyncMock(spec=ReviewStore)
    store.replay_draft.return_value = None
    storage = AsyncMock(spec=GitStorage)
    with pytest.raises(CaseValidationError):
        await SaveReviewDraft(identity=identity, store=store, storage=storage).execute(
            uuid4(),
            draft(
                DraftComment(
                    body="Invalid", path=path, line=line, end_line=end_line, side=DiffSide.NEW if path else None
                )
            ),
            "bad-range",
        )
    storage.files.assert_not_awaited()
    store.save_draft.assert_not_awaited()
