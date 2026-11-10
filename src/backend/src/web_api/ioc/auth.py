from dishka import Provider, Scope, alias, provide

from src.adapters.access_token.cryptographer import FernetAccessTokenCryptographer
from src.adapters.data_gateways.auth import DefaultAuthRepository
from src.adapters.password_utils import ArgonPasswordUtils
from src.application.auth_identity.interactor import (
    AuthenticateToken,
    GetCurrentUser,
    GrantWelcomeCredit,
    RegisterByEmail,
    SignInByEmail,
    SignOut,
)
from src.application.auth_identity.ports import AccessTokenCryptographer, AuthRepository, PasswordUtils
from src.application.common.identity_provider import IdentityProvider
from src.web_api.identity_provider import WebApiIdentityProvider


class AuthProvider(Provider):
    passwords = provide(ArgonPasswordUtils, provides=PasswordUtils, scope=Scope.APP)
    cryptographer = provide(FernetAccessTokenCryptographer, provides=AccessTokenCryptographer, scope=Scope.APP)
    repository = provide(DefaultAuthRepository, provides=AuthRepository, scope=Scope.REQUEST)
    welcome = provide(GrantWelcomeCredit, scope=Scope.REQUEST)
    register = provide(RegisterByEmail, scope=Scope.REQUEST)
    sign_in = provide(SignInByEmail, scope=Scope.REQUEST)
    authenticate = provide(AuthenticateToken, scope=Scope.REQUEST)
    sign_out = provide(SignOut, scope=Scope.REQUEST)
    current_user = provide(GetCurrentUser, scope=Scope.REQUEST)
    identity = provide(WebApiIdentityProvider, scope=Scope.REQUEST)
    identity_interface = alias(WebApiIdentityProvider, provides=IdentityProvider)
