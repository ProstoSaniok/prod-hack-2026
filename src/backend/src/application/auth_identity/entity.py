from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType, Self
from uuid import UUID, uuid4

from src.application.common.entity import Entity, to_entity
from src.application.user.entity import UserId

AuthIdentityId = NewType("AuthIdentityId", UUID)


class AuthMethod(StrEnum):
    EMAIL = "email"


@to_entity
class AuthIdentity(Entity[AuthIdentityId]):
    user_id: UserId
    method: AuthMethod
    identifier: str
    secret_key: str

    @classmethod
    def factory(cls, user_id: UserId, identifier: str, secret_key: str) -> Self:
        return cls(
            id=AuthIdentityId(uuid4()),
            created_at=datetime.now(tz=UTC),
            user_id=user_id,
            method=AuthMethod.EMAIL,
            identifier=identifier,
            secret_key=secret_key,
        )
