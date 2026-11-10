from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType, Self
from uuid import UUID, uuid4

from src.application.common.entity import Entity, to_entity

UserId = NewType("UserId", UUID)


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


@to_entity
class User(Entity[UserId]):
    role: UserRole = UserRole.USER

    @classmethod
    def factory(cls) -> Self:
        return cls(id=UserId(uuid4()), created_at=datetime.now(tz=UTC))
