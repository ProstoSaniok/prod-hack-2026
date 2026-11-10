from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    database: bool
    repository_storage: bool

    @property
    def ok(self) -> bool:
        return self.database and self.repository_storage


class ReadinessProbe(Protocol):
    async def check(self) -> ReadinessResult: ...
