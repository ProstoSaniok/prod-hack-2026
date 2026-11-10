import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.application.common.readiness import ReadinessResult
from src.application.repository_storage.ports import RepositoryStorage


class Readiness:
    """Dependency probes own no business transaction and expose no diagnostics."""

    def __init__(self, engine: AsyncEngine, storage: RepositoryStorage, *, timeout_seconds: float = 4.0) -> None:
        self._engine = engine
        self._storage = storage
        self._timeout_seconds = timeout_seconds
        self._limiter = asyncio.Semaphore(4)

    async def check(self) -> ReadinessResult:
        # Bound admission as well as I/O; do not queue arbitrary DB connections.
        try:
            async with asyncio.timeout(self._timeout_seconds), self._limiter:
                database, repository_storage = await asyncio.gather(self._database(), self._repository_storage())
                return ReadinessResult(database=database, repository_storage=repository_storage)
        except TimeoutError:
            return ReadinessResult(database=False, repository_storage=False)

    async def _database(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._engine.connect() as connection:
                    result = await connection.scalar(text("SELECT 1"))
                return bool(result == 1)
        except Exception:
            # Readiness must not expose connection strings or upstream exceptions.
            return False

    async def _repository_storage(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._storage.check_readiness()
        except Exception:
            return False
        else:
            return True
