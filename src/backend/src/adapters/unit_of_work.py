from typing import override

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.unit_of_work import UnitOfWork


class DefaultUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def add(self, *entities: object) -> None:
        self._session.add_all(entities)
        await self._session.flush()

    @override
    async def commit(self) -> None:
        await self._session.commit()
