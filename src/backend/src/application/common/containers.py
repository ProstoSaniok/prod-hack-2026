from collections.abc import Container, Hashable, Sized
from typing import Any, Final, override

_SECRET_VALUE: Final = "********"  # noqa: S105


class SecretString(Container[bool], Hashable, Sized):
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @override
    def __hash__(self) -> int:
        return hash(self._value)

    @override
    def __len__(self) -> int:
        return len(self._value)

    @override
    def __eq__(self, value: object) -> bool:
        if isinstance(value, str):
            return self._value == value
        return NotImplemented

    @override
    def __contains__(self, value: object) -> Any:
        if isinstance(value, str):
            return value in self._value
        return NotImplemented

    @override
    def __str__(self) -> str:
        return _SECRET_VALUE

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(value={_SECRET_VALUE!r})>"

    def get_value(self) -> str:
        return self._value
