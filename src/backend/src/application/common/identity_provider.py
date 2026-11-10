from typing import Protocol

from src.application.user.entity import User


class IdentityProvider(Protocol):
    async def get_current_user(self) -> User: ...
