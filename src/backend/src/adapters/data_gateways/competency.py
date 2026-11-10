from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.adapters.data_gateways.base_repository import BaseRepository
from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.entity import Competency
from src.application.catalog.list_competencies import CatalogUnavailableError


class DefaultCompetencyReader(BaseRepository):
    async def list_competencies(self, *, limit: int, offset: int) -> list[Competency]:
        table = meta_data.tables["competencies"]
        statement = (
            select(Competency)
            .where(table.c.deleted_at.is_(None))
            .order_by(table.c.created_at.desc(), table.c.id.asc())
            .limit(limit)
            .offset(offset)
        )
        try:
            return await self._find_many(statement)
        except SQLAlchemyError:
            raise CatalogUnavailableError from None
