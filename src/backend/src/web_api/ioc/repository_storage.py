from collections.abc import AsyncIterable

import httpx
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.readiness import Readiness
from src.adapters.gitea.transport import GiteaTransport
from src.application.common.readiness import ReadinessProbe
from src.application.repository_storage.ports import RepositoryStorage


class RepositoryStorageProvider(Provider):
    @provide(scope=Scope.APP)
    async def client(self, configuration: GiteaConfiguration) -> AsyncIterable[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            # GiteaTransport applies the configured deadline to the entire request.
            # A second fixed 3s HTTP deadline truncated Git writes on local hosts.
            timeout=httpx.Timeout(configuration.request_timeout_seconds),
            transport=httpx.AsyncHTTPTransport(
                retries=0, trust_env=False, limits=httpx.Limits(max_connections=4, max_keepalive_connections=4)
            ),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            yield client

    @provide(scope=Scope.APP)
    def storage(self, client: httpx.AsyncClient, configuration: GiteaConfiguration) -> RepositoryStorage:
        return GiteaTransport(client, configuration)

    @provide(scope=Scope.APP, provides=ReadinessProbe)
    def readiness(self, engine: AsyncEngine, storage: RepositoryStorage) -> Readiness:
        return Readiness(engine, storage)
