from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.adapters.unit_of_work import DefaultUnitOfWork
from src.application.common.unit_of_work import UnitOfWork
from src.web_api.configuration import DatabaseConfiguration


class ConnectionProvider(Provider):
    @provide(scope=Scope.APP)
    async def engine(self, configuration: DatabaseConfiguration) -> AsyncIterable[AsyncEngine]:
        engine = create_async_engine(configuration.url.get_value(), pool_pre_ping=True)
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.REQUEST)
    async def async_session(self, engine: AsyncEngine) -> AsyncIterable[AsyncSession]:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            yield session

    unit_of_work = provide(DefaultUnitOfWork, provides=UnitOfWork, scope=Scope.REQUEST)
