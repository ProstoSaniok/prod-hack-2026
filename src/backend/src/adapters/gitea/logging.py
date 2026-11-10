import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import override

_private_request: ContextVar[bool] = ContextVar("prodguild_private_git_request", default=False)
# Exact emitters in pinned HTTPX 0.28.1 / httpcore 1.0.9. Parent logger filters
# do not cover propagated records, so cover each transport emitter explicitly.
_HTTP_LOGGERS = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


class _PrivateRequestFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        return not _private_request.get()


@contextmanager
def private_http_logs() -> Iterator[None]:
    """Suppress only this request context, without changing shared logger levels.

    Each call owns a distinct filter; concurrent clients cannot remove each
    other's filters. Unrelated request tasks keep their normal logs. Cleanup
    also runs on cancellation, leaving no app-global filter behind.
    """
    request_filter = _PrivateRequestFilter()
    loggers = [logging.getLogger(name) for name in _HTTP_LOGGERS]
    for logger in loggers:
        logger.addFilter(request_filter)
    token = _private_request.set(True)
    try:
        yield
    finally:
        _private_request.reset(token)
        for logger in loggers:
            logger.removeFilter(request_filter)
