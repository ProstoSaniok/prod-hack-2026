"""APP-scoped production factory. Lifespan owns/awaits the ManagedWorker."""

from hashlib import sha256
from uuid import uuid4

import httpx
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.data_gateways.review_projection import SqlReviewProjectionStore
from src.adapters.data_gateways.tables import meta_data
from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.review_projection import GiteaReviewProjectionStorage
from src.adapters.gitea.transport import GiteaTransport
from src.adapters.operations.worker import ManagedWorker
from src.application.review.projection import ReviewProjectionRunner


def make_review_projection_worker(
    engine: AsyncEngine, client: httpx.AsyncClient, configuration: GiteaConfiguration, *, enabled: bool = True
) -> ManagedWorker:
    """No startup DB/network I/O; missing Gitea settings yields an idle runner.

    Caller must register/migrate review_git_projections before enabling. The HTTP
    client is the existing APP-owned retry-free RepositoryStorageProvider client.
    Tests may set enabled=False without needing dummy network/storage credentials.
    """
    configured = bool(configuration.internal_api_url.get_value() and configuration.api_token.get_value())
    instance = sha256(configuration.internal_api_url.get_value().rstrip("/").encode()).hexdigest()
    runner = ReviewProjectionRunner(
        SqlReviewProjectionStore(engine, instance, meta_data),
        GiteaReviewProjectionStorage(GiteaTransport(client, configuration)),
        owner=f"prodguild-review-projection-{uuid4().hex}",
        enabled=enabled and configured,
    )
    return ManagedWorker(runner, idle_seconds=1, shutdown_seconds=95)


class ReviewProjectionProvider(Provider):
    @provide(scope=Scope.APP)
    def worker(
        self, engine: AsyncEngine, client: httpx.AsyncClient, configuration: GiteaConfiguration
    ) -> ManagedWorker:
        return make_review_projection_worker(engine, client, configuration)
