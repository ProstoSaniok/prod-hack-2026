from dataclasses import dataclass

from cryptography.fernet import Fernet

from src.application.common.containers import SecretString


@dataclass(frozen=True, slots=True)
class AccessTokenConfiguration:
    crypto_key: SecretString
    expires_in: int = 86400

    def __post_init__(self) -> None:
        Fernet(self.crypto_key.get_value())
        if self.expires_in <= 0:
            raise ValueError("Access token lifetime must be positive")
