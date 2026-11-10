"""Stable public error envelopes; domain errors never import HTTP details."""

from uuid import uuid4

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from src.application.auth_identity.errors import AuthError, ForbiddenError
from src.application.catalog.management import CaseConflictError, CaseNotFoundError, CaseValidationError
from src.application.operations.entity import SourceConflictError
from src.application.operations.idempotency import (
    IdempotencyValidationError,
    PayloadMismatchError,
    RequestInProgressError,
    ResponseCaptureError,
)
from src.application.profile.queries import ProfileNotFoundError
from src.application.project.snapshot_reading import SnapshotTooLargeError
from src.application.repository_storage.git import StorageConflictError, StorageValidationError
from src.application.repository_storage.ports import StorageError
from src.application.wallet.queries import WalletNotFoundError
from src.web_api.http.requests import CaseBodyTooLargeError, LearningBodyTooLargeError


def error_response(status: int, code: str, message: str, *, bearer: bool = False) -> JSONResponse:
    request_id = str(uuid4())
    headers = {"X-Request-ID": request_id}
    if bearer:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status, content={"code": code, "message": message, "request_id": request_id}, headers=headers
    )


class CatalogErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


def case_error(error: Exception) -> Response:
    if isinstance(error, StorageValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid text snapshot")
    if isinstance(error, SourceConflictError):
        return error_response(409, "IDEMPOTENCY_CONFLICT", "Request key was used with different input")
    if isinstance(error, CaseConflictError):
        return error_response(409, error.code, str(error))
    if isinstance(error, (SQLAlchemyError, ResponseCaptureError)):
        return error_response(503, "DEPENDENCY_UNAVAILABLE", "Case storage temporarily unavailable")
    errors = (
        ((AuthError,), 401, "UNAUTHENTICATED", "Authentication required"),
        ((ForbiddenError,), 403, "FORBIDDEN", "Organizer capability required"),
        ((CaseNotFoundError,), 404, "NOT_FOUND", "Case not found"),
        ((PayloadMismatchError,), 409, "IDEMPOTENCY_CONFLICT", "Request key was used with different input"),
        ((RequestInProgressError,), 409, "OPERATION_IN_PROGRESS", "Request is already in progress"),
        ((CaseBodyTooLargeError,), 413, "PAYLOAD_TOO_LARGE", "Case request exceeds 65536 bytes"),
        (
            (RequestValidationError, CaseValidationError, IdempotencyValidationError),
            422,
            "VALIDATION_ERROR",
            "Invalid case input",
        ),
    )
    for types, status, code, message in errors:
        if isinstance(error, types):
            return error_response(status, code, message, bearer=status == 401)
    return error_response(503, "DEPENDENCY_UNAVAILABLE", "Case storage temporarily unavailable")


def learning_error(error: Exception) -> Response:
    if isinstance(error, SnapshotTooLargeError):
        return error_response(413, "PAYLOAD_TOO_LARGE", "Pinned text response exceeds 2 MiB; use paginated reads")
    if isinstance(error, LearningBodyTooLargeError):
        return error_response(413, "PAYLOAD_TOO_LARGE", "Learning request exceeds 8 MiB")
    if isinstance(error, StorageValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid text file input")
    if isinstance(error, (StorageConflictError, SourceConflictError)):
        return error_response(409, "SOURCE_CONFLICT", "Repository source or version changed")
    if isinstance(error, StorageError):
        return error_response(503, "DEPENDENCY_UNAVAILABLE", "Repository storage temporarily unavailable")
    return case_error(error)


def review_error(error: Exception) -> Response:
    if isinstance(error, ForbiddenError):
        return error_response(403, "FORBIDDEN", "Reviewer access required")
    if isinstance(error, CaseNotFoundError):
        return error_response(404, "NOT_FOUND", "Review assignment or pinned file not found")
    if isinstance(error, (RequestValidationError, CaseValidationError, IdempotencyValidationError)):
        return error_response(422, "VALIDATION_ERROR", "Invalid review input")
    return learning_error(error)


def catalog_error(error: Exception) -> Response:
    if isinstance(error, AuthError):
        return error_response(401, "UNAUTHENTICATED", "Authentication required", bearer=True)
    if isinstance(error, RequestValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid catalogue pagination")
    return error_response(503, "DEPENDENCY_UNAVAILABLE", "Catalogue temporarily unavailable")


def my_skills_error(error: Exception) -> Response:
    if isinstance(error, AuthError):
        return error_response(401, "UNAUTHENTICATED", "Authentication required", bearer=True)
    if isinstance(error, RequestValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid skills pagination")
    return error_response(503, "DEPENDENCY_UNAVAILABLE", "Skills temporarily unavailable")


def wallet_error(error: Exception) -> Response:
    if isinstance(error, AuthError):
        return error_response(401, "UNAUTHENTICATED", "Authentication required", bearer=True)
    if isinstance(error, RequestValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid wallet pagination")
    if isinstance(error, WalletNotFoundError):
        return error_response(404, "NOT_FOUND", "Wallet not found; sign in to initialize your account")
    return error_response(503, "DEPENDENCY_UNAVAILABLE", "Wallet temporarily unavailable")


def profile_error(error: Exception) -> Response:
    if isinstance(error, (ProfileNotFoundError, WalletNotFoundError)):
        return error_response(404, "NOT_FOUND", "Profile or wallet is not initialized")
    if isinstance(error, AuthError):
        return error_response(401, "UNAUTHENTICATED", "Authentication required", bearer=True)
    if isinstance(error, ForbiddenError):
        return error_response(403, "FORBIDDEN", "Participant capability required")
    if isinstance(error, RequestValidationError):
        return error_response(422, "VALIDATION_ERROR", "Invalid history pagination")
    return error_response(503, "DEPENDENCY_UNAVAILABLE", "Profile storage temporarily unavailable")


async def auth_error_handler(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication failed"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def forbidden_error_handler(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Forbidden"})


async def conflict_error_handler(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "User already exists"})


async def auth_unavailable_handler(request: Request, exception: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Authentication service unavailable"})
