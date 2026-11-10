from dataclasses import dataclass
from typing import dataclass_transform, override


@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
)
def to_error[T](cls: type[T]) -> type[T]:
    return dataclass(
        kw_only=True,
        eq=False,
        repr=False,
        match_args=False,
    )(cls)


class ApplicationError(Exception):
    """Base for expected application failures; contains no HTTP or infrastructure policy."""

    code: str = "APPLICATION_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def message(self) -> str:
        return str(self)


class NotFoundError(ApplicationError):
    code: str = "NOT_FOUND"


class ValidationError(ApplicationError, ValueError):
    code: str = "VALIDATION_ERROR"


class ConflictError(ApplicationError):
    code: str = "CONFLICT"


class DependencyUnavailableError(ApplicationError):
    code: str = "DEPENDENCY_UNAVAILABLE"


@to_error
class EntityAlreadyDeletedError(ApplicationError):
    entity_name: str

    @override
    def __str__(self) -> str:
        return f"Entity {self.entity_name!r} already deleted"
