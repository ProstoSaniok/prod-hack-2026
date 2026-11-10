from typing import Protocol

from src.application.common.errors import DependencyUnavailableError, NotFoundError
from src.application.common.identity_provider import IdentityProvider
from src.application.user.entity import UserId
from src.application.wallet.entity import LedgerEntry, Wallet


class WalletUnavailableError(DependencyUnavailableError):
    pass


class WalletNotFoundError(NotFoundError):
    pass


class WalletReader(Protocol):
    async def get_wallet(self, owner_id: UserId) -> Wallet | None: ...

    async def list_entries(self, owner_id: UserId, *, limit: int, offset: int) -> list[LedgerEntry]: ...


class WalletQueries:
    def __init__(self, identity: IdentityProvider, reader: WalletReader) -> None:
        self.identity = identity
        self.reader = reader

    async def get_wallet(self) -> Wallet:
        user = await self.identity.get_current_user()
        wallet = await self.reader.get_wallet(user.id)
        if wallet is None:
            raise WalletNotFoundError
        return wallet

    async def list_entries(self, *, limit: int = 20, offset: int = 0) -> list[LedgerEntry]:
        user = await self.identity.get_current_user()
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid wallet pagination")
        if offset > 9_223_372_036_854_775_807:
            return []
        return await self.reader.list_entries(user.id, limit=limit, offset=offset)
