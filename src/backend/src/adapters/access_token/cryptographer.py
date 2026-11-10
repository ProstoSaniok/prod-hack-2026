from datetime import timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from src.adapters.access_token.config import AccessTokenConfiguration
from src.application.access_token.entity import AccessTokenId


class FernetAccessTokenCryptographer:
    def __init__(self, configuration: AccessTokenConfiguration) -> None:
        self._fernet = Fernet(configuration.crypto_key.get_value())
        self._expires_in = timedelta(seconds=configuration.expires_in)

    @property
    def expires_in(self) -> timedelta:
        return self._expires_in

    def crypto(self, token_id: AccessTokenId) -> str:
        return self._fernet.encrypt(str(token_id).encode()).decode()

    def decrypto(self, raw_token: str) -> AccessTokenId | None:
        try:
            return AccessTokenId(UUID(self._fernet.decrypt(raw_token.encode()).decode()))
        except (InvalidToken, ValueError, UnicodeError):
            return None
