from typing import Protocol

from src.application.catalog.entity import Competency
from src.application.common.errors import DependencyUnavailableError
from src.application.common.identity_provider import IdentityProvider


class CatalogUnavailableError(DependencyUnavailableError):
    """The catalogue cannot currently be read."""


class CompetencyReader(Protocol):
    async def list_competencies(self, *, limit: int, offset: int) -> list[Competency]: ...


class ListCompetencies:
    def __init__(self, identity: IdentityProvider, reader: CompetencyReader) -> None:
        self.identity = identity
        self.reader = reader

    async def execute(self, *, limit: int = 20, offset: int = 0) -> list[Competency]:
        await self.identity.get_current_user()
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid catalogue pagination")
        # PostgreSQL OFFSET is a signed BIGINT. Larger valid API offsets are
        # beyond the supported result space, rather than a dependency failure.
        if offset > 9_223_372_036_854_775_807:
            return []
        return await self.reader.list_competencies(limit=limit, offset=offset)
