"""General PR comments, never unresearched native inline-review positions."""

import asyncio
import hashlib
import json
from collections.abc import Callable, Coroutine
from contextlib import suppress
from functools import wraps
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter

from src.adapters.gitea.git import GiteaGitStorage, _Model, _Owner, _PullRequest, _branch, _name, _sha
from src.application.operations.recovery import DependencyError
from src.application.operations.work import FailureCode
from src.application.repository_storage.git import StorageConflictError
from src.application.repository_storage.ports import StorageError, StorageIdentity
from src.application.review.projection import EXTERNAL_DEADLINE_SECONDS, MAX_BODY_BYTES, PublishedReviewProjection


class _Comment(_Model):
    id: int = Field(gt=0)
    body: str = Field(max_length=MAX_BODY_BYTES)
    user: _Owner
    issue_url: str = Field(max_length=8192)


_COMMENTS: TypeAdapter[list[_Comment]] = TypeAdapter(list[_Comment])


def _projection_errors[**P, T](
    operation: Callable[P, Coroutine[object, object, T]],
) -> Callable[P, Coroutine[object, object, T]]:
    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        code: FailureCode
        try:
            return await operation(*args, **kwargs)
        except DependencyError as failure:
            code = FailureCode(failure.code)
        except (StorageConflictError, ValueError, TypeError, KeyError):
            code = FailureCode.REJECTED
        except TimeoutError:
            code = FailureCode.TIMEOUT
        except (StorageError, OSError, RuntimeError):
            code = FailureCode.UNAVAILABLE
        # No raw upstream response, credentials, or chained exception may escape.
        raise DependencyError(code)

    return run


class GiteaReviewProjectionStorage(GiteaGitStorage):
    """Reuse existing read-only Git identity/ref/PR verification, not provisioning.

    This adapter never calls ensure_snapshot/retain/ensure_pull_request and never
    changes repositories, refs, permissions, accounts or tokens. The application
    provides the durable single-POST intention and cross-process lease fence.
    """

    async def _target(self, projection: PublishedReviewProjection) -> tuple[str, StorageIdentity]:
        _name(projection.repository_name)
        _branch(projection.base_branch)
        _branch(projection.head_branch)
        _sha(projection.base_sha)
        _sha(projection.head_sha)
        if (
            projection.base_branch == projection.head_branch
            or projection.base_sha == projection.head_sha
            or any(
                type(value) is not int or value < 1
                for value in (
                    projection.repository_id,
                    projection.service_owner_id,
                    projection.pull_number,
                    projection.pull_id,
                )
            )
        ):
            raise StorageConflictError
        identity = await self._transport.current_user()
        if identity.id != projection.service_owner_id:
            raise StorageConflictError
        repository = await self._repository(identity, projection.repository_name)
        if repository is None or repository.id != projection.repository_id:
            raise StorageConflictError
        path = self._path(identity, projection.repository_name)
        await self._retained_branch(path, projection.base_branch, projection.base_sha)
        await self._retained_branch(path, projection.head_branch, projection.head_sha)
        pull = _PullRequest.model_validate_json(await self._get(path + f"/pulls/{projection.pull_number}", large=True))
        if pull.number != projection.pull_number or pull.id != projection.pull_id:
            raise StorageConflictError
        self._check_pull_request(
            pull,
            repository,
            identity,
            {
                "base": projection.base_branch,
                "head": projection.head_branch,
                "body": projection.pull_marker,
                "title": "prodguild:" + hashlib.sha256(projection.pull_marker.encode()).hexdigest(),
            },
            projection.base_sha,
            projection.head_sha,
        )
        return path, identity

    @staticmethod
    def _check_comment(
        comment: _Comment, projection: PublishedReviewProjection, path: str, identity: StorageIdentity
    ) -> None:
        issue_url = urlsplit(comment.issue_url)
        if (
            comment.body != projection.body
            or comment.user.id != identity.id
            or comment.user.login != identity.login
            or issue_url.scheme not in {"http", "https"}
            or not issue_url.netloc
            or issue_url.path != "/api/v1" + path + f"/issues/{projection.pull_number}"
            or issue_url.query
            or issue_url.fragment
            or issue_url.username is not None
        ):
            raise StorageConflictError

    async def _find_comment(
        self, projection: PublishedReviewProjection, path: str, identity: StorageIdentity
    ) -> int | None:
        found: _Comment | None = None
        seen: set[int] = set()
        marker_identity = f"prodguild:published-review:v1:{projection.review_id}:{projection.revision_id}:"
        for page in range(1, 21):
            comments = _COMMENTS.validate_json(
                await self._get(
                    path + f"/issues/{projection.pull_number}/comments?limit=50&page={page}",
                    large=True,
                )
            )
            if len(comments) > 50:
                raise StorageError
            for comment in comments:
                if comment.id in seen:
                    raise StorageError
                seen.add(comment.id)
                if marker_identity in comment.body:
                    if found is not None:
                        raise StorageConflictError
                    self._check_comment(comment, projection, path, identity)
                    found = comment
            if not comments:
                if found is None:
                    return None
                # Read the concrete ID as well; list pagination alone is not a receipt.
                detail = _Comment.model_validate_json(
                    await self._get(path + f"/issues/comments/{found.id}", large=True)
                )
                if detail != found:
                    raise StorageConflictError
                self._check_comment(detail, projection, path, identity)
                return detail.id
        raise StorageError

    @_projection_errors
    async def lookup(self, projection: PublishedReviewProjection) -> int | None:
        async with asyncio.timeout(EXTERNAL_DEADLINE_SECONDS), self._lock:
            path, identity = await self._target(projection)
            observed = await self._find_comment(projection, path, identity)
            if observed is not None:
                # Reconciliation is a success path too: reject a repo/ref/PR that
                # changed during pagination/read-back, just as after a fresh POST.
                await self._target(projection)
            return observed

    @_projection_errors
    async def publish(self, projection: PublishedReviewProjection) -> int:
        async with asyncio.timeout(EXTERNAL_DEADLINE_SECONDS), self._lock:
            path, identity = await self._target(projection)
            existing = await self._find_comment(projection, path, identity)
            if existing is not None:
                return existing
            # The caller committed a permanent post_started_at before entering.
            # There is no transport retry, even on timeout/5xx/lost response.
            with suppress(StorageError):
                await self._transport.request(
                    "POST",
                    "/api/v1" + path + f"/issues/{projection.pull_number}/comments",
                    content=json.dumps({"body": projection.body}, ensure_ascii=False, separators=(",", ":")).encode(),
                    max_response_bytes=2_097_152,
                )
            observed = await self._find_comment(projection, path, identity)
            if observed is None:
                raise DependencyError(FailureCode.UNCERTAIN)
            # A changed/public/foreign PR must never be accepted as successful.
            await self._target(projection)
            return observed
