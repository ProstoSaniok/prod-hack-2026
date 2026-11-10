import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import NoReturn, override
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from sqlalchemy import event
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    TimeoutError as SqlTimeoutError,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.adapters.data_gateways.review_projection import SqlReviewProjectionStore
from src.adapters.data_gateways.tables import meta_data
from src.adapters.gitea import review_projection as gitea_projection
from src.adapters.gitea.review_projection import GiteaReviewProjectionStorage
from src.adapters.operations.worker import ManagedWorker
from src.application.common.containers import SecretString
from src.application.operations.recovery import DependencyError, RunResult
from src.application.operations.work import FailureCode, WorkState
from src.application.review import projection as application_projection
from src.application.review.projection import (
    MAX_BODY_BYTES,
    PROJECTION_POLICY,
    PublishedReviewProjection,
    ReviewProjectionRunner,
)
from src.web_api.configuration import (
    AccessTokenConfiguration,
    Configuration,
    DatabaseConfiguration,
    ServerConfiguration,
)
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc
from tests.support.review_projection import (
    MemoryProjectionStorage,
    MemoryProjectionStore,
    MockGitea,
    projection,
    transport,
)


def test_deterministic_bounded_safe_feedback_and_pinned_explicit_positions() -> None:
    item = projection()
    attack = item.marker + "\n```\n<script>alert(1)</script>\n[link](https://evil.invalid)\n@all"
    item = replace(item, content=replace(item.content, strengths=attack))
    body = item.body
    assert body == item.body
    assert body.count(item.marker) == 1
    assert "<script>" not in body
    assert "&lt;!--" in body
    assert "````text" in body
    assert "Side: old; line: 2" in body
    assert "Side: new; line: 3" in body
    assert item.base_sha in body
    assert item.head_sha in body
    assert "LF-normalized" in body
    assert "quality" not in body
    assert "grant" not in body
    oversized = replace(item, content=replace(item.content, strengths="я" * MAX_BODY_BYTES))
    with pytest.raises(ValueError, match="512 KiB"):
        _ = oversized.body


def test_feedback_projection_retains_inclusive_range_and_legacy_single_line() -> None:
    item = projection()
    original, old, new = item.comments
    ranged = replace(item, comments=(original, old, replace(new, end_line=8)))
    assert "Side: old; line: 2." in ranged.body
    assert "Side: new; line: 3–8." in ranged.body
    assert ranged.body_hash != item.body_hash
    assert replace(ranged, comments=item.comments).body == item.body


@pytest.mark.parametrize("state", ["not-published", "wrong-subject", "wrong-revision", "wrong-source", "wrong-topic"])
async def test_invalid_source_never_performs_git_io(state: str) -> None:
    store = MemoryProjectionStore(None if state == "not-published" else projection())
    if state == "wrong-subject":
        store.message = replace(store.message, spec=replace(store.message.spec, subject_id=uuid4()))
    elif state == "wrong-revision":
        store.message = replace(store.message, spec=replace(store.message.spec, revision=2))
    elif state == "wrong-source":
        store.message = replace(store.message, spec=replace(store.message.spec, source_key=str(uuid4())))
    elif state == "wrong-topic":
        store.message = replace(store.message, spec=replace(store.message.spec, topic="review.private"))
    storage = MemoryProjectionStorage()
    assert await ReviewProjectionRunner(store, storage, owner="one").run_once() == RunResult.NEEDS_ATTENTION
    assert storage.lookups == storage.posts == 0
    assert store.message.work.last_error == FailureCode.REJECTED


async def test_restart_after_remote_success_and_db_failure_deduplicates() -> None:
    store, storage = MemoryProjectionStore(projection()), MemoryProjectionStorage()
    store.fail_finalize = True
    assert await ReviewProjectionRunner(store, storage, owner="first").run_once() == RunResult.IDLE
    assert store.message.work.state == WorkState.RUNNING
    assert store.post_started
    assert store.receipt is None
    assert storage.posts == 1
    store.now += timedelta(seconds=121)
    assert await ReviewProjectionRunner(store, storage, owner="restart").run_once() == RunResult.SUCCEEDED
    assert store.receipt == 77
    assert storage.posts == 1
    assert await ReviewProjectionRunner(store, storage, owner="another").run_once() == RunResult.IDLE


async def test_ambiguous_absence_never_blindly_posts_again_and_exhausts() -> None:
    class AmbiguousStorage(MemoryProjectionStorage):
        @override
        async def publish(self, projection: PublishedReviewProjection) -> int:
            self.posts += 1
            raise DependencyError(FailureCode.UNCERTAIN)

    store, storage = MemoryProjectionStore(projection()), AmbiguousStorage()
    runner = ReviewProjectionRunner(store, storage, owner="worker")
    for attempt in range(1, 6):
        result = await runner.run_once()
        assert result == (RunResult.NEEDS_ATTENTION if attempt == 5 else RunResult.RETRY_WAIT)
        assert store.message.work.attempts == attempt
        store.now += timedelta(seconds=60)
    assert storage.posts == 1
    assert store.message.work.last_error == FailureCode.UNCERTAIN


async def test_preflight_failure_is_retryable_without_consuming_post_intention() -> None:
    store, storage = MemoryProjectionStore(projection()), MemoryProjectionStorage()
    storage.failure = FailureCode.UNAVAILABLE
    runner = ReviewProjectionRunner(store, storage, owner="worker")
    assert await runner.run_once() == RunResult.RETRY_WAIT
    assert not store.post_started
    storage.failure = None
    store.now += timedelta(seconds=2)
    assert await runner.run_once() == RunResult.SUCCEEDED
    assert storage.posts == 1


async def test_stale_lease_cannot_finalize_remote_effect() -> None:
    store = MemoryProjectionStore(projection())

    class SlowStorage(MemoryProjectionStorage):
        @override
        async def publish(self, projection: PublishedReviewProjection) -> int:
            result = await super().publish(projection)
            store.now += timedelta(seconds=121)
            return result

    storage = SlowStorage()
    assert await ReviewProjectionRunner(store, storage, owner="old").run_once() == RunResult.LEASE_LOST
    assert store.receipt is None
    assert store.message.work.state == WorkState.RUNNING
    assert await ReviewProjectionRunner(store, storage, owner="new").run_once() == RunResult.SUCCEEDED
    assert storage.posts == 1


async def test_managed_worker_idles_when_disabled_and_awaits_shutdown() -> None:
    store, storage = MemoryProjectionStore(projection()), MemoryProjectionStorage()
    runner = ReviewProjectionRunner(store, storage, owner="disabled", enabled=False)
    async with ManagedWorker(runner, idle_seconds=0.001, shutdown_seconds=1) as worker:
        await asyncio.sleep(0.01)
    assert worker.closed
    assert worker.task is not None
    assert worker.task.done()
    assert store.message.work.attempts == storage.lookups == storage.posts == 0
    assert PROJECTION_POLICY.lease_for.total_seconds() == 120
    assert PROJECTION_POLICY.max_attempts == 5


async def test_unconfigured_production_app_worker_idles_without_database_or_git_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    engine = create_async_engine("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")

    def unexpected_io(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Unconfigured projection must not perform database or Git I/O")

    event.listen(engine.sync_engine, "do_connect", unexpected_io)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_io)) as git_client:

        class TestProvider(Provider):
            @provide(scope=Scope.APP, override=True)
            def database(self) -> AsyncEngine:
                return engine

            @provide(scope=Scope.APP, override=True)
            def client(self) -> httpx.AsyncClient:
                return git_client

        configuration = Configuration(
            server=ServerConfiguration(),
            database=DatabaseConfiguration(url=SecretString("unused")),
            access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
        )
        app = make_asgi_application(make_ioc(configuration, TestProvider()))
        try:
            async with app.router.lifespan_context(app):
                worker = await app.state.dishka_container.get(ManagedWorker)
                assert worker is await app.state.dishka_container.get(ManagedWorker)
                assert isinstance(worker.runner, ReviewProjectionRunner)
                assert not worker.runner.enabled
                assert await worker.runner.run_once() == RunResult.IDLE
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/healthcheck")).json() == {"ok": True}
                    assert (await client.get("/auth/me")).status_code == 401
            assert worker.closed
            assert worker.task is not None
            assert worker.task.done()
            assert not git_client.is_closed  # The APP HTTP provider, not the worker, owns it.
        finally:
            await engine.dispose()


async def test_entire_external_unit_has_a_wall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class BlockedStorage(MemoryProjectionStorage):
        @override
        async def lookup(self, projection: PublishedReviewProjection) -> int | None:
            await asyncio.sleep(10)
            return None

    monkeypatch.setattr(application_projection, "EXTERNAL_DEADLINE_SECONDS", 0.01)
    store, storage = MemoryProjectionStore(projection()), BlockedStorage()
    assert await ReviewProjectionRunner(store, storage, owner="deadline").run_once() == RunResult.RETRY_WAIT
    assert store.message.work.last_error == FailureCode.TIMEOUT
    assert not store.post_started
    assert storage.posts == 0


async def test_cancellation_keeps_intention_and_restart_reconciles_late_remote_success() -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    class BlockedStorage(MemoryProjectionStorage):
        @override
        async def publish(self, projection: PublishedReviewProjection) -> int:
            self.posts += 1
            started.set()
            await never.wait()
            return 77

    item = projection()
    store, storage = MemoryProjectionStore(item), BlockedStorage()
    task = asyncio.create_task(ReviewProjectionRunner(store, storage, owner="cancelled").run_once())
    async with asyncio.timeout(1):
        await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.post_started
    assert store.message.work.state == WorkState.RUNNING
    assert store.receipt is None
    storage.comments[item.body] = 77  # Server completed the cancelled POST later.
    store.now += timedelta(seconds=121)
    assert await ReviewProjectionRunner(store, storage, owner="restart").run_once() == RunResult.SUCCEEDED
    assert storage.posts == 1


async def test_mock_transport_lost_post_response_is_reconciled_and_restart_uses_one_comment() -> None:
    item = projection()
    mock = MockGitea(item)
    mock.lost_response = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock), trust_env=False) as client:
        storage = GiteaReviewProjectionStorage(transport(client))
        assert await storage.lookup(item) is None
        assert await storage.publish(item) == 77
        assert await GiteaReviewProjectionStorage(transport(client)).lookup(item) == 77
    assert len(mock.comments) == 1
    assert sum(request.method == "POST" for request in mock.requests) == 1
    assert all(request.method in {"GET", "POST"} for request in mock.requests)
    assert all("/comments" in request.url.path for request in mock.requests if request.method == "POST")


@pytest.mark.parametrize(
    "conflict", ["public", "wrong_sha", "wrong_pr", "foreign_author", "wrong_body", "wrong_comment_issue", "duplicate"]
)
async def test_mock_transport_rejects_wrong_private_identity_and_marker(conflict: str) -> None:
    item = projection()
    mock = MockGitea(item)
    if conflict in {"public", "wrong_sha", "wrong_pr", "foreign_author", "wrong_comment_issue"}:
        setattr(mock, conflict, True)
    mock.comments = [mock.comment(item.body + ("tampered" if conflict == "wrong_body" else ""))]
    if conflict == "duplicate":
        mock.comments.append({**mock.comments[0], "id": 78})
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock), trust_env=False) as client:
        with pytest.raises(DependencyError) as caught:
            await GiteaReviewProjectionStorage(transport(client)).lookup(item)
    assert caught.value.code == FailureCode.REJECTED
    assert caught.value.__context__ is None
    assert "private-token" not in str(caught.value)
    assert not any(request.method == "POST" for request in mock.requests)


async def test_mock_transport_reconciles_a_later_comment_page_without_posting() -> None:
    item = projection()
    mock = MockGitea(item)
    mock.comments = [mock.comment(item.body)]

    def paginated(request: httpx.Request) -> httpx.Response:
        if request.url.path == mock.path + f"/issues/{item.pull_number}/comments":
            mock.requests.append(request)
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(200, json=[{**mock.comment("Unrelated"), "id": i} for i in range(1, 51)])
            return httpx.Response(200, json=mock.comments if page == 2 else [])
        return mock(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(paginated)) as client:
        assert await GiteaReviewProjectionStorage(transport(client)).lookup(item) == 77
    assert [request.url.params["page"] for request in mock.requests if "page" in request.url.params] == ["1", "2", "3"]
    assert all(request.method == "GET" for request in mock.requests)


@pytest.mark.parametrize("failure", ["repeated", "incomplete", "oversized"])
async def test_mock_transport_pagination_fails_closed_before_post(failure: str) -> None:
    item = projection()
    mock = MockGitea(item)

    def invalid_page(request: httpx.Request) -> httpx.Response:
        if request.url.path == mock.path + f"/issues/{item.pull_number}/comments":
            mock.requests.append(request)
            page = int(request.url.params["page"])
            ids = range(1, 52) if failure == "oversized" else range((page - 1) * 50 + 1, page * 50 + 1)
            if failure == "repeated":
                ids = range(1, 51)
            return httpx.Response(200, json=[{**mock.comment("Unrelated"), "id": i} for i in ids])
        return mock(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_page)) as client:
        with pytest.raises(DependencyError) as caught:
            await GiteaReviewProjectionStorage(transport(client)).lookup(item)
    assert caught.value.code == FailureCode.UNAVAILABLE
    assert len([request for request in mock.requests if "page" in request.url.params]) <= 20
    assert all(request.method == "GET" for request in mock.requests)


async def test_mock_transport_reconciliation_revalidates_private_target_after_readback() -> None:
    item = projection()
    mock = MockGitea(item)
    mock.comments = [mock.comment(item.body)]

    def changed_target(request: httpx.Request) -> httpx.Response:
        response = mock(request)
        if request.url.path == mock.path + "/issues/comments/77":
            mock.public = True
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(changed_target)) as client:
        with pytest.raises(DependencyError) as caught:
            await GiteaReviewProjectionStorage(transport(client)).lookup(item)
    assert caught.value.code == FailureCode.REJECTED
    assert all(request.method == "GET" for request in mock.requests)


async def test_adapter_wall_timeout_is_classified_as_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitea_projection, "EXTERNAL_DEADLINE_SECONDS", 0.01)
    item = projection()

    async def blocked(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
        with pytest.raises(DependencyError) as caught:
            await GiteaReviewProjectionStorage(transport(client)).lookup(item)
    assert caught.value.code == FailureCode.TIMEOUT
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            OperationalError("private SQL", {"token": "private-token"}, OSError("connection lost")),
            FailureCode.UNAVAILABLE,
        ),
        (InterfaceError("private SQL", {}, OSError("connection closed")), FailureCode.UNAVAILABLE),
        (DBAPIError("private SQL", {}, OSError("invalidated"), connection_invalidated=True), FailureCode.UNAVAILABLE),
        (DisconnectionError("private connection details"), FailureCode.UNAVAILABLE),
        (SqlTimeoutError("private pool details"), FailureCode.UNAVAILABLE),
        (IntegrityError("private SQL", {}, ValueError("constraint")), FailureCode.INTERNAL),
        (ProgrammingError("private SQL", {}, ValueError("invalid statement")), FailureCode.INTERNAL),
    ],
)
async def test_sql_store_classifies_and_sanitizes_database_failures(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, expected: FailureCode
) -> None:
    engine = create_async_engine("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")

    async def failed_execute(*args: object, **kwargs: object) -> NoReturn:
        raise failure

    monkeypatch.setattr(AsyncSession, "execute", failed_execute)
    store = SqlReviewProjectionStore(engine, "unused", meta_data)
    try:
        with pytest.raises(DependencyError) as caught:
            await store.load(MemoryProjectionStore(projection()).message)
        assert caught.value.code == expected
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
        assert "private" not in str(caught.value)
    finally:
        await engine.dispose()
