from fastapi import Request

from src.application.auth_identity.errors import AuthError
from src.application.auth_identity.interactor import AuthenticateToken
from src.application.user.entity import User


class WebApiIdentityProvider:
    def __init__(self, request: Request, auth: AuthenticateToken) -> None:
        self._request = request
        self._auth = auth

    def get_token(self) -> str:
        sections = self._request.headers.get("Authorization", "").split()
        if len(sections) != 2 or sections[0].casefold() != "bearer":
            raise AuthError
        return sections[1]

    async def get_current_user(self) -> User:
        user, _ = await self._auth.execute(self.get_token())
        return user
