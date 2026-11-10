import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import override

import httpx
import pytest

from src.adapters.gitea import GiteaConfiguration, GiteaTransport
from src.application.common.containers import SecretString
from src.application.repository_storage import RepositoryStorage, StorageError


@pytest.fixture
def configuration() -> GiteaConfiguration:
    return GiteaConfiguration(
        internal_api_url=SecretString("http://gitea.internal:3000"),
        api_token=SecretString("private-test-token"),
    )


async def test_identity_and_readiness(configuration: GiteaConfiguration) -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.method == "GET"
        assert not request.url.query
        assert "cookie" not in request.headers
        assert "x-client-secret" not in request.headers
        if request.url.path == "/api/healthz":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"status": "pass", "description": "Gitea"})
        assert request.url.path == "/api/v1/user"
        assert request.headers["authorization"] == "token private-test-token"
        return httpx.Response(200, json={"id": 7, "login": "prodguild-service", "ignored": "extra"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        auth=("unwanted", "credentials"),
        headers={"X-Client-Secret": "not-for-gitea"},
        cookies={"session": "unwanted"},
        params={"unwanted": "query"},
        follow_redirects=True,
    ) as client:
        storage: RepositoryStorage = GiteaTransport(client, configuration)
        identity = await storage.current_user()
        assert identity.id == 7
        assert identity.login == "prodguild-service"
        await storage.check_readiness()
    assert paths == ["/api/v1/user", "/api/healthz", "/api/v1/user"]
    assert "private-test-token" not in repr(configuration)
    assert "gitea.internal" not in repr(configuration)


async def test_request_logs_do_not_expose_internal_origin(
    configuration: GiteaConfiguration, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = logging.getLogger("httpx")
    previous_filters = logger.filters.copy()
    # Earlier Alembic fileConfig() can disable pre-existing library loggers.
    monkeypatch.setattr(logger, "disabled", False)
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": 7, "login": "service"}))
        ) as client:
            await GiteaTransport(client, configuration).current_user()
        logger.info("Unrelated request still logged")
    assert "gitea.internal" not in caplog.text
    assert "private-test-token" not in caplog.text
    assert "Unrelated request still logged" in caplog.text
    assert logger.filters == previous_filters


@pytest.mark.parametrize("status", [401, 302])
async def test_auth_failure_and_redirect_are_not_followed(configuration: GiteaConfiguration, status: int) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://elsewhere.invalid"}, text="private-test-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(StorageError, match="Repository storage is unavailable") as caught:
            await GiteaTransport(client, configuration).current_user()
    assert len(requests) == 1
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class _Body(httpx.AsyncByteStream):
    def __init__(self, *, slow: bool = False) -> None:
        self.slow = slow
        self.closed = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b" " * 200
        if self.slow:
            await asyncio.sleep(1)
        yield b" " * 200

    @override
    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("slow", [False, True])
async def test_stream_size_and_wall_deadline(configuration: GiteaConfiguration, *, slow: bool) -> None:
    body = _Body(slow=slow)
    configuration = replace(configuration, max_response_bytes=256, request_timeout_seconds=0.05)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))) as client:
        with pytest.raises(StorageError) as caught:
            await GiteaTransport(client, configuration).current_user()
    assert body.closed
    assert caught.value.__context__ is None


async def test_invalid_settings_fail_at_operation_time(configuration: GiteaConfiguration) -> None:
    def unexpected_request(_: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid configuration must not send a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
        for settings in (
            GiteaConfiguration(),
            replace(configuration, internal_api_url=SecretString("https://user:secret@gitea.invalid")),
            replace(configuration, internal_api_url=SecretString("https://gitea.invalid/subpath")),
            replace(configuration, internal_api_url=SecretString("https://gitea.invalid/..")),
            replace(configuration, api_token=SecretString("bad\r\ntoken")),
            replace(configuration, request_timeout_seconds=float("inf")),
        ):
            storage = GiteaTransport(client, settings)
            with pytest.raises(StorageError):
                await storage.check_readiness()


async def test_invalid_payload_and_network_error_are_sanitized(configuration: GiteaConfiguration) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/healthz":
            raise httpx.ConnectError("private-test-token http://gitea.internal raw upstream body")
        return httpx.Response(200, json={"id": True, "login": "prodguild-service"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        storage = GiteaTransport(client, configuration)
        for operation in (storage.current_user, storage.check_readiness):
            with pytest.raises(StorageError) as caught:
                await operation()
            assert caught.value.__context__ is None
            assert caught.value.__cause__ is None
            assert "private-test-token" not in str(caught.value)
