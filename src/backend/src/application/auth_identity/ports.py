from datetime import timedelta
from typing import Protocol

from src.application.access_token.entity import AccessToken, AccessTokenId
from src.application.auth_identity.entity import AuthIdentity
from src.application.common.containers import SecretString
from src.application.user.capability import ProductCapability
from src.application.user.entity import User, UserId


class AuthRepository(Protocol):
    async def find_capabilities(self, user_id: UserId) -> list[ProductCapability]: ...

    async def find_identity(self, email: str) -> AuthIdentity | None: ...

    async def find_user(self, user_id: UserId) -> User | None: ...

    async def find_token(self, token_id: AccessTokenId) -> AccessToken | None: ...

    async def register(self, user: User, identity: AuthIdentity, token: AccessToken) -> None: ...

    async def ensure_participant(self, user: User) -> None: ...


class PasswordUtils(Protocol):
    async def hash(self, password: SecretString) -> str: ...

    async def verify(self, password: SecretString, hashed_password: str) -> bool: ...


class AccessTokenCryptographer(Protocol):
    def crypto(self, token_id: AccessTokenId) -> str: ...

    def decrypto(self, raw_token: str) -> AccessTokenId | None: ...

    @property
    def expires_in(self) -> timedelta: ...
