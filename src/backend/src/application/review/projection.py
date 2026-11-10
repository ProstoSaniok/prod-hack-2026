"""Published-only Git feedback projection, independent of publication/rewards."""

import asyncio
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from src.application.operations.entity import OutboxMessage
from src.application.operations.recovery import DependencyError, RunResult
from src.application.operations.work import FailureCode, RetryPolicy
from src.application.repository_storage.git import validate_path
from src.application.review.entity import DiffSide, RevisionContent

TOPIC = "review.published"
MAX_BODY_BYTES = 512 * 1024
EXTERNAL_DEADLINE_SECONDS = 90
# RetryPolicy reserves two calls; this runner instead bounds the ENTIRE unit to 90s.
PROJECTION_POLICY = RetryPolicy(lease_for=timedelta(seconds=120), io_timeout=timedelta(seconds=45))


@dataclass(frozen=True, kw_only=True)
class ProjectionComment:
    text: str
    path: str | None = None
    side: DiffSide | None = None
    line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Empty published comment")
        if self.path is not None:
            validate_path(self.path)
        if (self.side is None) != (self.line is None) or (
            self.line is not None and (self.path is None or type(self.line) is not int or self.line < 1)
        ):
            raise ValueError("Invalid retained comment position")
        if self.end_line is not None and (
            type(self.end_line) is not int or self.line is None or self.end_line < self.line
        ):
            raise ValueError("Invalid retained comment range")


@dataclass(frozen=True, kw_only=True)
class PublishedReviewProjection:
    review_id: UUID
    revision_id: UUID
    revision_number: int
    repository_name: str
    repository_id: int
    service_owner_id: int
    pull_number: int
    pull_id: int
    pull_marker: str
    base_branch: str
    base_sha: str
    head_branch: str
    head_sha: str
    content: RevisionContent
    comments: tuple[ProjectionComment, ...]

    @property
    def marker(self) -> str:
        return f"<!-- prodguild:published-review:v1:{self.review_id}:{self.revision_id}:{self.revision_number} -->"

    @property
    def body(self) -> str:
        return render_feedback(self)

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def _literal(text: str) -> str:
    # Escape HTML even inside code, so user input can never echo a hidden marker.
    # A delimiter longer than every user run prevents Markdown fence breakout.
    escaped = html.escape(text, quote=False)
    longest = max((len(run) for run in re.findall(r"`+", escaped)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{escaped}\n{fence}"


def render_feedback(projection: PublishedReviewProjection) -> str:
    content = projection.content
    sections = [
        projection.marker,
        "# Published prodguild feedback",
        f"Review {projection.review_id}; revision {projection.revision_number} ({projection.revision_id}).",
        f"Pinned comparison: B `{projection.base_sha}` → C `{projection.head_sha}`.",
        "Positions use LF-normalized retained files: old = B, new = C; lines are 1-based.",
        f"Score: {content.score:f}/100. Verdict: {content.verdict.value}.",
    ]
    for title, text in (
        ("Strengths", content.strengths),
        ("Problems", content.problems),
        ("Next steps", content.next_steps),
    ):
        sections.extend(("## " + title, _literal(text)))
    for index, comment in enumerate(projection.comments, 1):
        sections.append(f"## Comment {index}")
        if comment.path is not None:
            sections.extend(("Path:", _literal(comment.path)))
        if comment.side is not None:
            position = str(comment.line) if comment.end_line is None else f"{comment.line}–{comment.end_line}"
            sections.append(f"Side: {comment.side.value}; line: {position}.")
        else:
            sections.append("General comment (no inline position).")
        sections.append(_literal(comment.text))
    body = "\n\n".join(sections) + "\n"
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("Published projection exceeds 512 KiB")
    return body


class ReviewProjectionStorage(Protocol):
    async def lookup(self, projection: PublishedReviewProjection) -> int | None:
        """Validate private repo/PR/retained refs and exact service-authored marker/body."""
        ...

    async def publish(self, projection: PublishedReviewProjection) -> int:
        """One POST maximum, followed by validated read-back; never blindly retry."""
        ...


class ReviewProjectionStore(Protocol):
    async def claim(self, *, owner: str, policy: RetryPolicy) -> OutboxMessage | None: ...

    async def load(self, message: OutboxMessage) -> PublishedReviewProjection | None:
        """Only exact PUBLISHED revision; private quality/draft/evidence is never read."""
        ...

    async def reserve_post(self, message: OutboxMessage, projection: PublishedReviewProjection) -> bool:
        """Commit a fenced permanent mutation intention BEFORE the single POST.

        False means a prior POST may still finish, or this claim is stale. A new
        worker may reconcile GETs but must never send another POST for this event.
        """
        ...

    async def finish(
        self,
        message: OutboxMessage,
        *,
        projection: PublishedReviewProjection | None,
        external_id: int | None,
        error: FailureCode | None,
        policy: RetryPolicy,
    ) -> RunResult:
        """Atomically save fenced work state and successful metadata receipt."""
        ...


class ReviewProjectionRunner:
    def __init__(
        self, store: ReviewProjectionStore, storage: ReviewProjectionStorage, *, owner: str, enabled: bool = True
    ) -> None:
        self.store, self.storage, self.owner, self.enabled = store, storage, owner, enabled

    async def run_once(self) -> RunResult:
        if not self.enabled:
            return RunResult.IDLE
        try:
            return await self._run_once()
        except Exception:
            # DB outage/finalization failure must not kill the ManagedWorker, log
            # driver parameters or release a possibly-applied intention. Lease
            # expiry/restart will reconcile remote state before doing anything.
            return RunResult.IDLE

    async def _run_once(self) -> RunResult:
        policy = PROJECTION_POLICY
        message = await self.store.claim(owner=self.owner, policy=policy)
        if message is None:
            return RunResult.IDLE
        projection: PublishedReviewProjection | None = None
        external_id: int | None = None
        error: FailureCode | None = None
        try:
            async with asyncio.timeout(EXTERNAL_DEADLINE_SECONDS):
                projection, external_id = await self._project(message)
        except DependencyError as failure:
            error = FailureCode(failure.code)
        except TimeoutError:
            error = FailureCode.TIMEOUT
        except Exception:
            error = FailureCode.INTERNAL
        # CancelledError intentionally propagates: durable lease/intention survives.
        return await self.store.finish(
            message, projection=projection, external_id=external_id, error=error, policy=policy
        )

    async def _project(self, message: OutboxMessage) -> tuple[PublishedReviewProjection, int]:
        projection = await self.store.load(message)
        if (
            projection is None
            or message.spec.topic != TOPIC
            or message.spec.subject_id != projection.review_id
            or message.spec.source_key != str(projection.revision_id)
            or message.spec.revision != projection.revision_number
        ):
            raise DependencyError(FailureCode.REJECTED)
        # Render/size validation is complete before any external I/O.
        _ = projection.body
        external_id = await self.storage.lookup(projection)
        if external_id is None:
            if not await self.store.reserve_post(message, projection):
                raise DependencyError(FailureCode.UNCERTAIN)
            external_id = await self.storage.publish(projection)
        if type(external_id) is not int or external_id < 1:
            raise DependencyError(FailureCode.REJECTED)
        return projection, external_id
