from hashlib import sha256

import httpx
from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning import DefaultLearningStore
from src.adapters.data_gateways.learning_reader import DefaultLearningReader
from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.git import GiteaGitStorage
from src.adapters.gitea.transport import GiteaTransport
from src.application.project.learning import Learning, LearningStore
from src.application.project.reading import LearningReader, LearningReads
from src.application.project.usecases import ReferenceReadinessStore, SetReferenceReady
from src.application.repository_storage.git import GitStorage


class LearningProvider(Provider):
    @provide(scope=Scope.APP)
    def git(self, client: httpx.AsyncClient, configuration: GiteaConfiguration) -> GitStorage:
        return GiteaGitStorage(GiteaTransport(client, configuration))

    @provide(scope=Scope.REQUEST)
    def store(self, session: AsyncSession, configuration: GiteaConfiguration) -> DefaultLearningStore:
        instance = sha256(configuration.internal_api_url.get_value().rstrip("/").encode()).hexdigest()
        return DefaultLearningStore(session, instance)

    store_port = alias(DefaultLearningStore, provides=LearningStore)
    learning = provide(Learning, scope=Scope.REQUEST)
    reader = provide(DefaultLearningReader, scope=Scope.REQUEST)
    reader_port = alias(DefaultLearningReader, provides=LearningReader)
    readiness_port = alias(DefaultLearningReader, provides=ReferenceReadinessStore)
    set_reference_ready = provide(SetReferenceReady, scope=Scope.REQUEST)
    reads = provide(LearningReads, scope=Scope.REQUEST)
