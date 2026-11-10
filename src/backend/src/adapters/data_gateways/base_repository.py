from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


@dataclass(slots=True)
class BaseRepository:
    _session: AsyncSession

    async def _find_one_or_none(self, statement: Select[tuple[T]]) -> T | None:
        """Выполняет запрос и возвращает один результат или None."""
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _find_many(self, statement: Select[tuple[T]]) -> list[T]:
        """Выполняет запрос и возвращает список результатов."""
        result = await self._session.scalars(statement)
        return list(result)

    def _add(self, entity: T) -> None:
        """Добавляет сущность в сессию."""
        self._session.add(entity)

    def _add_all(self, entities: list[T]) -> None:
        """Добавляет список сущностей в сессию."""
        self._session.add_all(entities)

    async def _flush(self) -> None:
        """Выполняет flush для синхронизации с БД без commit."""
        await self._session.flush()
