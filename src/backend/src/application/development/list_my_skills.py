from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.common.data_structure import to_data_structure
from src.application.common.errors import DependencyUnavailableError
from src.application.common.identity_provider import IdentityProvider
from src.application.development.entity import SkillGrade
from src.application.user.entity import UserId


@to_data_structure
class MySkill:
    competency_id: UUID
    name: str
    grade: SkillGrade
    displayed_value: Decimal


class MySkillsUnavailableError(DependencyUnavailableError):
    pass


class MySkillsReader(Protocol):
    async def list_skills(self, owner_id: UserId, *, limit: int, offset: int) -> list[MySkill]: ...


class ListMySkills:
    def __init__(self, identity: IdentityProvider, reader: MySkillsReader) -> None:
        self.identity = identity
        self.reader = reader

    async def execute(self, *, limit: int = 20, offset: int = 0) -> list[MySkill]:
        user = await self.identity.get_current_user()
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid skills pagination")
        if offset > 9_223_372_036_854_775_807:
            return []
        return await self.reader.list_skills(user.id, limit=limit, offset=offset)
