from abc import abstractmethod
from typing import Protocol


class UnitOfWork(Protocol):
    @abstractmethod
    async def add(self, *entities: object) -> None:
        raise NotImplementedError

    @abstractmethod
    async def commit(self) -> None:
        raise NotImplementedError
