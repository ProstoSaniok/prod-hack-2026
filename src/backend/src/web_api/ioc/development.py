from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.development import DefaultDevelopmentStore
from src.application.development.usecases import ApplyReviewerApplication, AssessReviewerApplication
from src.application.development.workflow import DevelopmentQueries, DevelopmentStore


class DevelopmentProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def store(self, session: AsyncSession) -> DefaultDevelopmentStore:
        return DefaultDevelopmentStore(session)

    store_port = alias(DefaultDevelopmentStore, provides=DevelopmentStore)
    workflow = provide(DevelopmentQueries, scope=Scope.REQUEST)

    apply_reviewer_application = provide(ApplyReviewerApplication, scope=Scope.REQUEST)
    assess_reviewer_application = provide(AssessReviewerApplication, scope=Scope.REQUEST)
