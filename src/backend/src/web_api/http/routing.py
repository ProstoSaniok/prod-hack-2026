"""One Dishka/HTTP boundary with explicit domain-specific policies."""

from collections.abc import Callable, Coroutine
from typing import ClassVar, override

from dishka.integrations.fastapi import DishkaRoute
from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

from src.application.auth_identity.errors import AuthError, AuthUnavailableError, ForbiddenError
from src.application.catalog.list_competencies import CatalogUnavailableError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError, CaseValidationError
from src.application.development.list_my_skills import MySkillsUnavailableError
from src.application.operations.entity import SourceConflictError
from src.application.operations.idempotency import (
    IdempotencyValidationError,
    PayloadMismatchError,
    RequestInProgressError,
    ResponseCaptureError,
)
from src.application.repository_storage.ports import StorageError
from src.application.wallet.errors import WalletInvariantError
from src.application.wallet.queries import WalletNotFoundError, WalletUnavailableError
from src.web_api.http.errors import (
    case_error,
    catalog_error,
    learning_error,
    my_skills_error,
    profile_error,
    review_error,
    wallet_error,
)
from src.web_api.http.requests import CaseBodyTooLargeError, CaseRequest, LearningRequest, require_unique_query


class ApiRoute(DishkaRoute):
    handled_errors: ClassVar[tuple[type[Exception], ...]] = (Exception,)
    cache_control: ClassVar[bool] = True

    async def prepare(self, request: Request) -> Request:
        return request

    @staticmethod
    def map_error(error: Exception) -> Response:
        return case_error(error)

    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(await self.prepare(request))
            except self.handled_errors as error:
                response = self.map_error(error)
            if self.cache_control:
                response.headers["Cache-Control"] = "no-store"
            return response

        return handler


class CatalogRoute(ApiRoute):
    cache_control = False
    handled_errors: ClassVar[tuple[type[Exception], ...]] = (
        AuthError,
        RequestValidationError,
        CatalogUnavailableError,
        SQLAlchemyError,
    )
    map_error = staticmethod(catalog_error)


class MySkillsRoute(ApiRoute):
    handled_errors: ClassVar[tuple[type[Exception], ...]] = (
        AuthError,
        RequestValidationError,
        MySkillsUnavailableError,
        AuthUnavailableError,
        SQLAlchemyError,
    )
    map_error = staticmethod(my_skills_error)


class CaseRoute(ApiRoute):
    handled_errors: ClassVar[tuple[type[Exception], ...]] = (
        AuthError,
        ForbiddenError,
        CaseNotFoundError,
        CaseConflictError,
        RequestValidationError,
        CaseValidationError,
        IdempotencyValidationError,
        SQLAlchemyError,
        ResponseCaptureError,
        CaseBodyTooLargeError,
        StorageError,
        SourceConflictError,
        PayloadMismatchError,
        RequestInProgressError,
    )

    @override
    async def prepare(self, request: Request) -> Request:
        bounded = CaseRequest(request.scope, request.receive)
        if request.method in {"POST", "PATCH"}:
            await bounded.json()
        return bounded


class LearningRoute(ApiRoute):
    map_error = staticmethod(learning_error)

    @override
    async def prepare(self, request: Request) -> Request:
        bounded = LearningRequest(request.scope, request.receive)
        await bounded.prepare(body_allowed=self.body_field is not None)
        return bounded


class ReviewRoute(LearningRoute):
    map_error = staticmethod(review_error)


class DevelopmentRoute(ApiRoute):
    map_error = staticmethod(review_error)

    @override
    async def prepare(self, request: Request) -> Request:
        bounded = CaseRequest(request.scope, request.receive)
        require_unique_query(bounded)
        if request.method == "POST":
            await bounded.json()
        return bounded


class ProfileRoute(ApiRoute):
    map_error = staticmethod(profile_error)


class WalletRoute(ApiRoute):
    handled_errors: ClassVar[tuple[type[Exception], ...]] = (
        AuthError,
        RequestValidationError,
        WalletNotFoundError,
        WalletUnavailableError,
        WalletInvariantError,
        SQLAlchemyError,
    )
    map_error = staticmethod(wallet_error)
