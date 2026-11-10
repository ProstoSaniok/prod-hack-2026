import asyncio
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.gitea.logging import private_http_logs
from src.application.repository_storage.ports import StorageError, StorageIdentity


class _Health(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    status: Literal["pass"]


class _Identity(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)


class GiteaTransport:
    """Bounded transport; the injected client's lifetime is caller-owned.

    The owner must disable environment proxies and transport retries. Requests do
    not inherit client auth, headers, cookies, query parameters or redirect policy.
    HTTPX/httpcore request logs are suppressed only in this request's context.

    Source-verified, not pinned-live-fixture acceptance: Gitea v28.1.0 registers
    GET /api/healthz in routers/web/web.go; routers/web/healthcheck/check.go emits
    {"status":"pass", ...} with HTTP 200 on success (424 on failure).
    See https://github.com/go-gitea/gitea/tree/v28.1.0/routers/web/healthcheck.
    """

    def __init__(self, client: httpx.AsyncClient, configuration: GiteaConfiguration) -> None:
        self._client = client
        self._configuration = configuration
        self._limiter: asyncio.Semaphore | None = None

    async def check_readiness(self) -> None:
        """Require both service health and successful credentialed identity lookup."""
        await self._operation(check_health=True)

    async def current_user(self) -> StorageIdentity:
        return await self._operation(check_health=False)

    @property
    def operation_timeout_seconds(self) -> float:
        self._settings()
        return self._configuration.request_timeout_seconds

    async def request(
        self, method: str, path: str, *, content: bytes | None = None, max_response_bytes: int = 65_536
    ) -> tuple[int, bytes]:
        """Internal API request with an explicit 2 MiB ceiling for base64 text.

        No retry or status interpretation is performed; callers must verify state
        after ambiguous mutations. Readiness retains its separate configured cap.
        """
        try:
            origin, token = self._settings()
            self._validate_request(method, path, content, max_response_bytes)
            if self._limiter is None:
                self._limiter = asyncio.Semaphore(self._configuration.max_concurrency)
            async with asyncio.timeout(self._configuration.request_timeout_seconds), self._limiter:
                request = httpx.Request(
                    method,
                    origin + path,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/json",
                        "Authorization": "token " + token,
                    },
                    content=content,
                )
                with private_http_logs():
                    response = await self._client.send(request, stream=True, auth=None, follow_redirects=False)
                    try:
                        if response.is_redirect:
                            raise StorageError
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise StorageError
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > max_response_bytes:
                                raise StorageError
                            body.extend(chunk)
                        result = response.status_code, bytes(body)
                    finally:
                        await response.aclose()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError, TimeoutError, RuntimeError, StorageError):
            pass
        else:
            return result
        raise StorageError

    def _validate_request(self, method: str, path: str, content: bytes | None, maximum: int) -> None:
        if (
            method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            or not path.startswith("/api/v1/")
            or "#" in path
            or "\\" in path
            or len(path) > 8192
            or not 256 <= maximum <= 2_097_152
            or (content is not None and len(content) > 2_097_152)
        ):
            raise StorageError

    async def _current_user(self, origin: str, token: str) -> StorageIdentity:
        payload = _Identity.model_validate_json(await self._get(origin + "/api/v1/user", token=token))
        return StorageIdentity(id=payload.id, login=payload.login)

    async def _operation(self, *, check_health: bool) -> StorageIdentity:
        try:
            origin, token = self._settings()
            if self._limiter is None:
                self._limiter = asyncio.Semaphore(self._configuration.max_concurrency)
            # A single wall deadline covers admission, pool, both probes and body.
            async with asyncio.timeout(self._configuration.request_timeout_seconds), self._limiter:
                if check_health:
                    _Health.model_validate_json(await self._get(origin + "/api/healthz"))
                identity = await self._current_user(origin, token)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError, TimeoutError, RuntimeError, StorageError):
            pass
        else:
            return identity
        # Raise outside the handler: neither raw cause nor context escapes.
        raise StorageError

    def _settings(self) -> tuple[str, str]:
        configuration = self._configuration
        timeout = configuration.request_timeout_seconds
        if (
            type(timeout) not in {int, float}
            or not 0.05 <= timeout <= 30
            or type(configuration.max_response_bytes) is not int
            or not 256 <= configuration.max_response_bytes <= 1_048_576
            or type(configuration.max_concurrency) is not int
            or not 1 <= configuration.max_concurrency <= 32
        ):
            raise StorageError

        raw_origin = configuration.internal_api_url.get_value()
        token = configuration.api_token.get_value()
        if (
            not raw_origin
            or len(raw_origin) > 2048
            or any(character.isspace() or ord(character) < 32 for character in raw_origin)
            or any(character in raw_origin for character in ("\\", "?", "#", "@", "%"))
            or not token
            or len(token) > 4096
            or any(not 33 <= ord(character) <= 126 for character in token)
        ):
            raise StorageError
        if urlsplit(raw_origin).path not in {"", "/"}:
            raise StorageError
        origin = httpx.URL(raw_origin)
        if origin.scheme not in {"http", "https"} or not origin.host or origin.raw_path != b"/":
            raise StorageError
        return str(origin).rstrip("/"), token

    async def _get(self, url: str, *, token: str | None = None) -> bytes:
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if token is not None:
            headers["Authorization"] = "token " + token
        # Construct directly: build_request()/stream() merge client-level secrets.
        request = httpx.Request("GET", url, headers=headers)
        with private_http_logs():
            response = await self._client.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code != 200:
                    raise StorageError
                # Reject unexpected compression rather than allocating an unbounded
                # decompressor output before HTTPX can yield a decoded chunk.
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise StorageError
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self._configuration.max_response_bytes:
                        raise StorageError
                    body.extend(chunk)
                return bytes(body)
            finally:
                await response.aclose()
