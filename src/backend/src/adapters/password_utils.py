import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from src.application.common.containers import SecretString


class ArgonPasswordUtils:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    async def hash(self, password: SecretString) -> str:
        return await asyncio.to_thread(self._hasher.hash, password.get_value())

    async def verify(self, password: SecretString, hashed_password: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, hashed_password, password.get_value())
        except Argon2Error:
            return False
