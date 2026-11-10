from datetime import UTC, datetime, timedelta
from typing import NewType, Self
from uuid import UUID, uuid4

from src.application.common.entity import Entity, to_entity
from src.application.user.entity import UserId

AccessTokenId = NewType("AccessTokenId", UUID)


@to_entity
class AccessToken(Entity[AccessTokenId]):
    user_id: UserId
    expires_in: datetime
    revoked: bool = False

    @classmethod
    def factory(cls, user_id: UserId, expires_in: timedelta) -> Self:
        now = datetime.now(tz=UTC)
        return cls(id=AccessTokenId(uuid4()), created_at=now, user_id=user_id, expires_in=now + expires_in)

    def expired_predicate(self) -> bool:
        return self.expires_in <= datetime.now(tz=UTC) or self.revoked or self.deleted_at is not None

    def revoke(self) -> None:
        self.revoked = True
        self.deleted_at = datetime.now(tz=UTC)
