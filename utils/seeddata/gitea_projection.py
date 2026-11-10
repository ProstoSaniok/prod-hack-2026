"""Gitea 28 comment compatibility, enabled only by the temporary seed server."""

from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dishka import Provider, Scope, provide
from pydantic import Field, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.review_projection import SqlReviewProjectionStore
from src.adapters.data_gateways.tables import meta_data
from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.review_projection import GiteaReviewProjectionStorage, _Comment
from src.adapters.gitea.transport import GiteaTransport
from src.adapters.operations.worker import ManagedWorker
from src.application.repository_storage.git import StorageConflictError
from src.application.repository_storage.ports import StorageError, StorageIdentity
from src.application.review.projection import (
    PublishedReviewProjection,
    ReviewProjectionRunner,
)


class SeedComment(_Comment):
    pull_request_url: str = Field(default="", max_length=8192)


COMMENTS = TypeAdapter(list[SeedComment])


class SeedGiteaProjectionStorage(GiteaReviewProjectionStorage):
    """Retain production target fencing and single-POST recovery unchanged."""

    @staticmethod
    def check_comment(
        comment: SeedComment,
        projection: PublishedReviewProjection,
        path: str,
        identity: StorageIdentity,
    ) -> None:
        if (
            comment.body != projection.body
            or comment.user.id != identity.id
            or comment.user.login != identity.login
        ):
            raise StorageConflictError
        targets = (
            (comment.issue_url, "/api/v1" + path + f"/issues/{projection.pull_number}"),
            (
                comment.pull_request_url,
                path.removeprefix("/repos") + f"/pulls/{projection.pull_number}",
            ),
        )
        if not any(value for value, _ in targets):
            raise StorageConflictError
        for value, expected_path in targets:
            if not value:
                continue
            target = urlsplit(value)
            if (
                target.scheme not in {"http", "https"}
                or not target.netloc
                or target.path != expected_path
                or target.query
                or target.fragment
                or target.username is not None
            ):
                raise StorageConflictError

    async def _find_comment(
        self,
        projection: PublishedReviewProjection,
        path: str,
        identity: StorageIdentity,
    ) -> int | None:
        # Gitea 28 ignores page/limit here. The inherited transport bounds bytes.
        comments = COMMENTS.validate_json(
            await self._get(
                path + f"/issues/{projection.pull_number}/comments", large=True
            )
        )
        if len(comments) > 1000:
            raise StorageError
        found = None
        seen = set()
        marker = f"prodguild:published-review:v1:{projection.review_id}:{projection.revision_id}:"
        for comment in comments:
            if comment.id in seen:
                raise StorageError
            seen.add(comment.id)
            if marker in comment.body:
                if found is not None:
                    raise StorageConflictError
                self.check_comment(comment, projection, path, identity)
                found = comment
        if found is None:
            return None
        detail = SeedComment.model_validate_json(
            await self._get(path + f"/issues/comments/{found.id}", large=True)
        )
        if detail != found:
            raise StorageConflictError
        self.check_comment(detail, projection, path, identity)
        return detail.id


class SeedReviewProjectionProvider(Provider):
    @provide(scope=Scope.APP, override=True)
    def worker(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        configuration: GiteaConfiguration,
    ) -> ManagedWorker:
        configured = bool(
            configuration.internal_api_url.get_value()
            and configuration.api_token.get_value()
        )
        instance = sha256(
            configuration.internal_api_url.get_value().rstrip("/").encode()
        ).hexdigest()
        runner = ReviewProjectionRunner(
            SqlReviewProjectionStore(engine, instance, meta_data),
            SeedGiteaProjectionStorage(GiteaTransport(client, configuration)),
            owner=f"prodguild-seed-projection-{uuid4().hex}",
            enabled=configured,
        )
        return ManagedWorker(runner, idle_seconds=1, shutdown_seconds=95)
