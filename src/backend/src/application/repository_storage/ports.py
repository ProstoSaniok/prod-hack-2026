from typing import Protocol, override

from src.application.common.data_structure import to_data_structure
from src.application.common.errors import ApplicationError, to_error


@to_data_structure
class StorageIdentity:
    id: int
    login: str


@to_error
class StorageError(ApplicationError):
    @override
    def __str__(self) -> str:
        return "Repository storage is unavailable"


class RepositoryStorage(Protocol):
    """Read-only transport contract; readiness does not establish repository ACLs."""

    async def check_readiness(self) -> None: ...

    async def current_user(self) -> StorageIdentity: ...
