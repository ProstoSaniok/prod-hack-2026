from dataclasses import dataclass
from datetime import UTC, datetime

from src.application.access_token.entity import AccessToken
from src.application.auth_identity.entity import AuthIdentity
from src.application.auth_identity.errors import (
    AuthError,
    AuthUnavailableError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)
from src.application.auth_identity.ports import AccessTokenCryptographer, AuthRepository, PasswordUtils
from src.application.common.containers import SecretString
from src.application.common.identity_provider import IdentityProvider
from src.application.common.interactor import to_interactor
from src.application.common.unit_of_work import UnitOfWork
from src.application.user.capability import ProductCapability
from src.application.user.entity import User
from src.application.wallet.errors import WalletError
from src.application.wallet.ports import WalletTransaction
from src.application.wallet.service import WalletCollaborator


@to_interactor
class GrantWelcomeCredit:
    transaction: WalletTransaction

    async def execute(self, user: User) -> None:
        try:
            wallet = await WalletCollaborator.acquire(self.transaction, owner_ids=(user.id,))
            await wallet.welcome(user.id, now=datetime.now(UTC))
        except WalletError:
            raise AuthUnavailableError from None


@to_interactor
class RegisterByEmail:
    repository: AuthRepository
    passwords: PasswordUtils
    cryptographer: AccessTokenCryptographer
    unit_of_work: UnitOfWork
    welcome: GrantWelcomeCredit

    async def execute(self, email: str, password: SecretString) -> str:
        email = email.casefold()
        if await self.repository.find_identity(email) is not None:
            raise UserAlreadyExistsError
        user = User.factory()
        identity = AuthIdentity.factory(user.id, email, await self.passwords.hash(password))
        token = AccessToken.factory(user.id, self.cryptographer.expires_in)
        await self.repository.register(user, identity, token)
        await self.repository.ensure_participant(user)
        await self.welcome.execute(user)
        await self.unit_of_work.commit()
        return self.cryptographer.crypto(token.id)


@to_interactor
class SignInByEmail:
    repository: AuthRepository
    passwords: PasswordUtils
    cryptographer: AccessTokenCryptographer
    unit_of_work: UnitOfWork
    welcome: GrantWelcomeCredit

    async def execute(self, email: str, password: SecretString) -> str:
        identity = await self.repository.find_identity(email.casefold())
        if identity is None or not await self.passwords.verify(password, identity.secret_key):
            raise InvalidCredentialsError
        user = await self.repository.find_user(identity.user_id)
        if user is None:
            raise InvalidCredentialsError
        token = AccessToken.factory(user.id, self.cryptographer.expires_in)
        await self.unit_of_work.add(token)
        await self.repository.ensure_participant(user)
        # Existing accounts receive their missing, permanently deduplicated welcome
        # credit on this authenticated mutation. Wallet GETs remain read-only.
        await self.welcome.execute(user)
        await self.unit_of_work.commit()
        return self.cryptographer.crypto(token.id)


@to_interactor
class AuthenticateToken:
    repository: AuthRepository
    cryptographer: AccessTokenCryptographer

    async def execute(self, raw_token: str) -> tuple[User, AccessToken]:
        token_id = self.cryptographer.decrypto(raw_token)
        if token_id is None:
            raise AuthError
        token = await self.repository.find_token(token_id)
        if token is None or token.expired_predicate():
            raise AuthError
        user = await self.repository.find_user(token.user_id)
        if user is None:
            raise AuthError
        return user, token


@to_interactor
class SignOut:
    authenticate: AuthenticateToken
    unit_of_work: UnitOfWork

    async def execute(self, raw_token: str) -> None:
        _, token = await self.authenticate.execute(raw_token)
        token.revoke()
        await self.unit_of_work.commit()


@dataclass(frozen=True, kw_only=True)
class CurrentUser:
    user: User
    capabilities: tuple[ProductCapability, ...]


@to_interactor
class GetCurrentUser:
    identity: IdentityProvider
    repository: AuthRepository

    async def execute(self) -> CurrentUser:
        user = await self.identity.get_current_user()
        return CurrentUser(user=user, capabilities=tuple(await self.repository.find_capabilities(user.id)))
