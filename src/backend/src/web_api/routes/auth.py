from typing import Literal
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Response
from pydantic import BaseModel, EmailStr, Field, SecretStr

from src.application.auth_identity.interactor import GetCurrentUser, RegisterByEmail, SignInByEmail, SignOut
from src.application.common.containers import SecretString
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserRole
from src.web_api.identity_provider import WebApiIdentityProvider

router = APIRouter(route_class=DishkaRoute, prefix="/auth", tags=["Auth"])


class SignUpRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=8, max_length=1024)


class SignInRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str = Field(description="Opaque bearer access token")
    token_type: Literal["bearer"] = "bearer"  # noqa: S105


class CurrentUserResponse(BaseModel):
    id: UUID
    role: UserRole
    capabilities: list[ProductCapability]


@router.post("/sign_up/email")
async def sign_up(request: SignUpRequest, auth: FromDishka[RegisterByEmail]) -> TokenResponse:
    token = await auth.execute(str(request.email), SecretString(request.password.get_secret_value()))
    return TokenResponse(access_token=token)


@router.post("/sign_in/email")
async def sign_in(request: SignInRequest, auth: FromDishka[SignInByEmail]) -> TokenResponse:
    token = await auth.execute(str(request.email), SecretString(request.password.get_secret_value()))
    return TokenResponse(access_token=token)


@router.get("/me")
async def get_me(query: FromDishka[GetCurrentUser]) -> CurrentUserResponse:
    current = await query.execute()
    return CurrentUserResponse(id=current.user.id, role=current.user.role, capabilities=list(current.capabilities))


@router.post("/sign_out", status_code=204)
async def sign_out(auth: FromDishka[SignOut], identity: FromDishka[WebApiIdentityProvider]) -> Response:
    await auth.execute(identity.get_token())
    return Response(status_code=204)
