from uuid import uuid4

from cryptography.fernet import Fernet

from src.adapters.access_token.cryptographer import FernetAccessTokenCryptographer
from src.application.access_token.entity import AccessTokenId
from src.application.common.containers import SecretString
from src.web_api.configuration import AccessTokenConfiguration


def test_invalid_token_payload_is_rejected() -> None:
    key = Fernet.generate_key()
    cryptographer = FernetAccessTokenCryptographer(AccessTokenConfiguration(crypto_key=SecretString(key.decode())))
    token_id = AccessTokenId(uuid4())
    encrypted = cryptographer.crypto(token_id)
    assert cryptographer.decrypto(encrypted) == token_id
    assert cryptographer.decrypto(encrypted[:-3] + "xxx") is None
    assert cryptographer.decrypto(Fernet(key).encrypt(b"not-a-uuid").decode()) is None
    assert cryptographer.decrypto(Fernet(key).encrypt(b"\xff").decode()) is None
