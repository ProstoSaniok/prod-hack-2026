from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.case_management import DefaultCaseGateway
from src.adapters.operations.case_requests import CaseRequestJournal
from src.adapters.operations.sql_work import database_now
from src.application.catalog.management import CaseAccess, CaseGateway, CaseQueries
from src.application.catalog.usecases import CreateCase, DeleteCase, UpdateCase
from src.application.operations.clock import Clock
from src.application.operations.idempotency import RequestJournal
from src.web_api.case_requests import CaseRequests


class CaseProvider(Provider):
    gateway = provide(DefaultCaseGateway, provides=CaseGateway, scope=Scope.REQUEST)
    access = provide(CaseAccess, scope=Scope.REQUEST)
    queries = provide(CaseQueries, scope=Scope.REQUEST)
    create = provide(CreateCase, scope=Scope.REQUEST)
    update = provide(UpdateCase, scope=Scope.REQUEST)
    delete = provide(DeleteCase, scope=Scope.REQUEST)
    clock = alias(CaseRequestJournal, provides=Clock)
    journal = alias(CaseRequestJournal, provides=RequestJournal)
    requests = provide(CaseRequests, scope=Scope.REQUEST)

    @provide(scope=Scope.REQUEST)
    async def case_journal(self, session: AsyncSession) -> CaseRequestJournal:
        return CaseRequestJournal(session, await database_now(session))
