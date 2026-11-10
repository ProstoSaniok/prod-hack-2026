from src.application.common.errors import ApplicationError, to_error


@to_error
class AuthError(ApplicationError):
    code = "UNAUTHENTICATED"


@to_error
class InvalidCredentialsError(AuthError):
    code = "INVALID_CREDENTIALS"


@to_error
class UserAlreadyExistsError(ApplicationError):
    code = "USER_ALREADY_EXISTS"


@to_error
class ForbiddenError(ApplicationError):
    code = "FORBIDDEN"


@to_error
class AuthUnavailableError(ApplicationError):
    code = "DEPENDENCY_UNAVAILABLE"
