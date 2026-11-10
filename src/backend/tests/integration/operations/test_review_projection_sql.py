"""Isolated PostgreSQL publication/recovery; Git is always an in-memory fixture."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import override
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from psycopg.errors import SerializationFailure
from sqlalchemy import MetaData, event, func, select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.adapters.data_gateways.review_projection import SqlReviewProjectionStore
from src.adapters.data_gateways.review_projection_tables import register_review_projection_tables
from src.adapters.data_gateways.tables import meta_data
from src.adapters.gitea.config import GiteaConfiguration
from src.adapters.operations.sql_records import outbox_from_row
from src.adapters.operations.sql_work import SqlOutbox
from src.adapters.operations.worker import ManagedWorker
from src.application.common.containers import SecretString
from src.application.operations.entity import OutboxMessage, ProjectionSpec
from src.application.operations.recovery import DependencyError, RunResult
from src.application.operations.work import FailureCode, Work, WorkState
from src.application.repository_storage.git import GitStorage
from src.application.review.entity import ReviewState
from src.application.review.projection import (
    PROJECTION_POLICY,
    TOPIC,
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
from tests.support.learning import MemoryGit, learning_client
from tests.support.review_completion import complete, effects, final_draft, save
from tests.support.review_projection import MemoryProjectionStorage, MockGitea
from tests.support.review_quality import provisional
from tests.support.reviews import Scenario, claim, reviewer, submitted
from tests.web_api.e2e.conftest import auth_engine

__all__ = ["auth_engine", "learning_client"]


@pytest.fixture
def learning_storage(monkeypatch: pytest.MonkeyPatch) -> GitStorage:
    # Never opt into the older helper's live fixture, even if shell env is set.
    monkeypatch.delenv("TEST_GITEA_ORIGIN", raising=False)
    monkeypatch.delenv("TEST_GITEA_TOKEN", raising=False)
    return MemoryGit()


@pytest.fixture
async def store(auth_engine: AsyncEngine) -> SqlReviewProjectionStore:
    # Parent owns central registration/migration. Until integrated, the isolated
    # fixture creates only the new table from an independent metadata graph.
    metadata = MetaData()
    for table in meta_data.tables.values():
        table.to_metadata(metadata)
    if "review_git_projections" not in metadata.tables:
        receipt = register_review_projection_tables(metadata)
        async with auth_engine.begin() as connection:
            await connection.run_sync(receipt.create)
    return SqlReviewProjectionStore(auth_engine, hashlib.sha256(b"").hexdigest(), metadata)


async def published(client: AsyncClient, engine: AsyncEngine) -> tuple[Scenario, UUID]:
    scenario = await submitted(client, engine)
    headers = await reviewer(client, engine, scenario.skills, "projection-reviewer@example.com")
    assigned = await claim(client, headers)
    assert assigned.status_code == 201, assigned.text
    await save(client, assigned.headers["location"], headers, final_draft())
    created = await complete(client, assigned.headers["location"], headers)
    assert created.status_code == 201, created.text
    return scenario, UUID(created.json()["id"])


async def age_work(engine: AsyncEngine) -> None:
    table = meta_data.tables["outbox_messages"]
    async with engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.topic == TOPIC)
            .values(
                available_at=datetime.now(UTC) - timedelta(seconds=1),
                lease_until=None,
                lease_token=None,
                lease_owner=None,
                state=WorkState.RETRY_WAIT,
            )
        )


async def expire_claim(engine: AsyncEngine) -> None:
    table = meta_data.tables["outbox_messages"]
    async with engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.topic == TOPIC)
            .values(
                lease_until=datetime.now(UTC) - timedelta(seconds=1),
            )
        )


async def test_published_projection_uses_exact_revision_retained_refs_and_leaves_rewards_unchanged(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
) -> None:
    scenario, review_id = await published(learning_client, auth_engine)
    response = await learning_client.get(f"/api/v1/reviews/{review_id}", headers=scenario.author)
    assert response.status_code == 200
    assert response.json()["projection_status"] == "pending"
    assert response.json()["gitea_review_id"] is None
    assert response.json()["gitea_comment_id"] is None
    before = await effects(auth_engine)

    class InspectingStorage(MemoryProjectionStorage):
        @override
        async def lookup(self, projection: PublishedReviewProjection) -> int | None:
            # The claim is committed and NO row lock/session spans external I/O.
            async with auth_engine.begin() as connection:
                row = await connection.execute(
                    select(store.outbox).where(store.outbox.c.topic == TOPIC).with_for_update(nowait=True)
                )
                assert row.mappings().one()["state"] == WorkState.RUNNING
            assert projection.review_id == review_id
            assert projection.base_branch.startswith("prodguild/bases/")
            assert projection.head_branch.endswith(scenario.submission.rsplit("/", 1)[1])
            assert "Original strengths" in projection.body
            assert "PRIVATE EVIDENCE" not in projection.body
            assert "PRIVATE SEED" not in projection.body
            return await super().lookup(projection)

    storage = InspectingStorage()
    assert await ReviewProjectionRunner(store, storage, owner="one").run_once() == RunResult.SUCCEEDED
    assert await ReviewProjectionRunner(store, storage, owner="restart").run_once() == RunResult.IDLE
    assert storage.posts == 1
    after = await effects(auth_engine)
    assert {k: v for k, v in before.items() if k != "outbox_messages"} == {
        k: v for k, v in after.items() if k != "outbox_messages"
    }
    async with auth_engine.connect() as connection:
        receipt = (await connection.execute(select(store.receipts))).mappings().one()
        assert receipt["external_comment_id"] == 77
        assert receipt["completed_at"] is not None
        assert receipt["review_id"] == review_id
        assert receipt["post_started_at"] is not None
    response = await learning_client.get(f"/api/v1/reviews/{review_id}", headers=scenario.author)
    assert response.status_code == 200
    assert response.json()["pull_request_url"] is None
    assert response.json()["projection_status"] == "published"
    assert response.json()["gitea_review_id"] is None
    assert response.json()["gitea_comment_id"] == receipt["external_comment_id"] == 77
    assert response.headers["cache-control"] == "no-store"


async def test_git_outage_retries_but_never_rolls_back_publication_wallet_or_progression(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
) -> None:
    scenario, review_id = await published(learning_client, auth_engine)
    before = await effects(auth_engine)
    storage = MemoryProjectionStorage()
    storage.failure = FailureCode.UNAVAILABLE
    assert await ReviewProjectionRunner(store, storage, owner="outage").run_once() == RunResult.RETRY_WAIT
    after = await effects(auth_engine)
    assert before["wallets"] == after["wallets"]
    assert before["wallet_ledger"] == after["wallet_ledger"]
    assert before["progression_contributions"] == after["progression_contributions"]
    assert before["review_results"] == after["review_results"]
    assert after["outbox_messages"][0]["last_error"] == FailureCode.UNAVAILABLE
    response = await learning_client.get(f"/api/v1/reviews/{review_id}", headers=scenario.author)
    assert response.status_code == 200
    assert response.json()["projection_status"] == "retryable_failure"
    assert response.json()["gitea_comment_id"] is None
    await age_work(auth_engine)
    storage.failure = None
    assert await ReviewProjectionRunner(store, storage, owner="restart").run_once() == RunResult.SUCCEEDED
    assert storage.posts == 1


async def test_db_finalization_failure_after_remote_side_effect_reconciles_one_comment(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await published(learning_client, auth_engine)
    storage = MemoryProjectionStorage()
    original = SqlOutbox.save_leased

    async def fail_finish(self: SqlOutbox, message: OutboxMessage, *, token: UUID, now: datetime) -> bool:
        assert await original(self, message, token=token, now=now)
        # Failure AFTER the state UPDATE must roll the whole finalization back.
        raise SQLAlchemyError("never expose credentials or private SQL parameters")

    with monkeypatch.context() as patch:
        patch.setattr(SqlOutbox, "save_leased", fail_finish)
        assert await ReviewProjectionRunner(store, storage, owner="crash").run_once() == RunResult.IDLE
    async with auth_engine.connect() as connection:
        receipt = (await connection.execute(select(store.receipts))).mappings().one()
        assert receipt["post_started_at"] is not None
        assert receipt["external_comment_id"] is None
        assert (
            await connection.execute(select(store.outbox.c.state).where(store.outbox.c.topic == TOPIC))
        ).scalar_one() == WorkState.RUNNING
    await expire_claim(auth_engine)
    restarted = SqlReviewProjectionStore(auth_engine, store.instance_key, store.metadata)
    assert await ReviewProjectionRunner(restarted, storage, owner="restart").run_once() == RunResult.SUCCEEDED
    assert storage.posts == 1


async def test_concurrent_workers_claim_one_writer_and_stale_fence_refuses_finalization(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
) -> None:
    await published(learning_client, auth_engine)
    claims = await asyncio.gather(*(store.claim(owner=f"worker-{n}", policy=PROJECTION_POLICY) for n in range(2)))
    messages = [message for message in claims if message is not None]
    assert len(messages) == 1
    old = messages[0]
    item = await store.load(old)
    assert item is not None
    assert await store.reserve_post(old, item)
    await expire_claim(auth_engine)
    current = await store.claim(owner="new", policy=PROJECTION_POLICY)
    assert current is not None
    assert current.work.lease != old.work.lease
    assert not await store.reserve_post(old, item)
    assert (
        await store.finish(old, projection=item, external_id=77, error=None, policy=PROJECTION_POLICY)
        == RunResult.LEASE_LOST
    )
    async with auth_engine.connect() as connection:
        receipt = (await connection.execute(select(store.receipts))).mappings().one()
        assert receipt["external_comment_id"] is None
        assert receipt["completed_at"] is None
    storage = MemoryProjectionStorage()
    storage.comments[item.body] = 77  # prior remote effect arrived before restart
    assert await storage.lookup(item) == 77
    assert (
        await store.finish(current, projection=item, external_id=77, error=None, policy=PROJECTION_POLICY)
        == RunResult.SUCCEEDED
    )


@pytest.mark.parametrize("state", [ReviewState.PENDING, ReviewState.AWAITING_QUALITY, ReviewState.NEEDS_REVISION])
async def test_fabricated_publication_event_for_private_review_never_performs_git_io(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    state: ReviewState,
) -> None:
    scenario = await submitted(learning_client, auth_engine)
    headers, _ = await provisional(learning_client, auth_engine, scenario.skills)
    assigned = await claim(learning_client, headers)
    await save(learning_client, assigned.headers["location"], headers, final_draft(marker="PRIVATE pending"))
    created = await complete(learning_client, assigned.headers["location"], headers)
    assert created.status_code == 201
    assert created.json()["status"] == "pending_quality_check"
    response = await learning_client.get(created.headers["location"], headers=headers)
    assert response.status_code == 200
    assert response.json()["projection_status"] == "not_eligible"
    assert response.json()["gitea_review_id"] is None
    assert response.json()["gitea_comment_id"] is None
    assert (await learning_client.get(created.headers["location"], headers=scenario.author)).status_code == 404
    body, now = created.json(), datetime.now(UTC) - timedelta(seconds=1)
    message = OutboxMessage(
        id=uuid4(),
        spec=ProjectionSpec(
            topic=TOPIC,
            source_key=body["revision_id"],
            subject_id=UUID(body["id"]),
            revision=body["revision_number"],
        ),
        work=Work(created_at=now, available_at=now),
    )
    async with store.sessions.begin() as session:
        await session.execute(update(meta_data.tables["review_results"]).values(state=state))
        await SqlOutbox(session, store.outbox).enqueue(message)
    storage = MemoryProjectionStorage()
    assert await ReviewProjectionRunner(store, storage, owner="private").run_once() == RunResult.NEEDS_ATTENTION
    assert storage.lookups == storage.posts == 0
    async with auth_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(store.receipts)) == 0


async def test_only_publication_topic_is_claimed_and_exact_source_revision_is_required(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
) -> None:
    await published(learning_client, auth_engine)
    message = await store.claim(owner="load", policy=PROJECTION_POLICY)
    assert message is not None
    assert await store.load(message) is not None
    for spec in (
        replace(message.spec, subject_id=uuid4()),
        replace(message.spec, source_key=str(uuid4())),
        replace(message.spec, revision=2),
        replace(message.spec, topic="review.private"),
    ):
        assert await store.load(replace(message, spec=spec)) is None
    now = datetime.now(UTC)
    async with store.sessions.begin() as session:
        await SqlOutbox(session, store.outbox).enqueue(
            OutboxMessage(
                id=uuid4(),
                spec=ProjectionSpec(topic="review.private", source_key="private", subject_id=uuid4(), revision=1),
                work=Work(created_at=now, available_at=now),
            )
        )
    assert await store.claim(owner="cannot-claim-other-topic", policy=PROJECTION_POLICY) is None


@pytest.mark.parametrize("late_success", [False, True])
async def test_sql_durable_post_intention_reconciles_once_or_exhausts_without_resending(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    *,
    late_success: bool,
) -> None:
    scenario, review_id = await published(learning_client, auth_engine)
    before = await effects(auth_engine)

    class AmbiguousStorage(MemoryProjectionStorage):
        @override
        async def publish(self, projection: PublishedReviewProjection) -> int:
            # A different connection must see the committed intention BEFORE I/O.
            async with auth_engine.connect() as connection:
                receipt = (await connection.execute(select(store.receipts))).mappings().one()
                assert receipt["post_started_at"] is not None
                assert receipt["completed_at"] is None
            self.posts += 1
            if late_success:
                self.comments[projection.body] = 77
            raise DependencyError(FailureCode.UNCERTAIN)

    storage = AmbiguousStorage()
    runner = ReviewProjectionRunner(store, storage, owner="first")
    assert await runner.run_once() == RunResult.RETRY_WAIT
    await age_work(auth_engine)
    restarted = ReviewProjectionRunner(
        SqlReviewProjectionStore(auth_engine, store.instance_key, store.metadata), storage, owner="restart"
    )
    if late_success:
        assert await restarted.run_once() == RunResult.SUCCEEDED
        expected_status = "published"
    else:
        for attempt in range(2, 6):
            assert await restarted.run_once() == (RunResult.NEEDS_ATTENTION if attempt == 5 else RunResult.RETRY_WAIT)
            if attempt != 5:
                await age_work(auth_engine)
        assert await restarted.run_once() == RunResult.IDLE
        expected_status = "needs_attention"
    assert storage.posts == 1
    response = await learning_client.get(f"/api/v1/reviews/{review_id}", headers=scenario.author)
    assert response.status_code == 200
    assert response.json()["projection_status"] == expected_status
    assert response.json()["gitea_review_id"] is None
    assert response.json()["gitea_comment_id"] == (77 if late_success else None)
    after = await effects(auth_engine)
    assert {key: rows for key, rows in before.items() if key != "outbox_messages"} == {
        key: rows for key, rows in after.items() if key != "outbox_messages"
    }


async def test_receipt_identity_conflict_rolls_back_success_state_and_restart_reconciles(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
) -> None:
    await published(learning_client, auth_engine)
    message = await store.claim(owner="first", policy=PROJECTION_POLICY)
    assert message is not None
    item = await store.load(message)
    assert item is not None
    assert await store.reserve_post(message, item)
    changed = replace(item, content=replace(item.content, strengths="Changed receipt must not be accepted"))
    with pytest.raises(ValueError, match="receipt identity changed"):
        await store.finish(message, projection=changed, external_id=77, error=None, policy=PROJECTION_POLICY)
    async with auth_engine.connect() as connection:
        receipt = (await connection.execute(select(store.receipts))).mappings().one()
        assert receipt["body_hash"] == item.body_hash
        assert receipt["completed_at"] is None
        assert receipt["external_comment_id"] is None
        assert (
            await connection.execute(select(store.outbox.c.state).where(store.outbox.c.id == message.id))
        ).scalar_one() == WorkState.RUNNING
    await expire_claim(auth_engine)
    storage = MemoryProjectionStorage()
    storage.comments[item.body] = 77
    assert await ReviewProjectionRunner(store, storage, owner="restart").run_once() == RunResult.SUCCEEDED
    assert storage.posts == 0


async def test_configured_app_lifespan_worker_projects_with_real_sql_and_mock_transport(
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    origin = "http://gitea.test"
    active_store = SqlReviewProjectionStore(auth_engine, hashlib.sha256(origin.encode()).hexdigest(), store.metadata)
    mock: MockGitea | None = None

    async def remote(request: Request) -> Response:
        nonlocal mock
        if mock is None:
            async with auth_engine.connect() as connection:
                row = (
                    (await connection.execute(select(store.outbox).where(store.outbox.c.topic == TOPIC)))
                    .mappings()
                    .one()
                )
            item = await active_store.load(outbox_from_row(row))
            assert item is not None
            mock = MockGitea(item)
            mock.lost_response = True
        return mock(request)

    memory_git = MemoryGit()
    async with AsyncClient(transport=MockTransport(remote), trust_env=False) as upstream:

        class Dependencies(Provider):
            @provide(scope=Scope.APP, override=True)
            def engine(self) -> AsyncEngine:
                return auth_engine

            @provide(scope=Scope.APP, override=True)
            def client(self) -> AsyncClient:
                return upstream

            @provide(scope=Scope.APP, override=True)
            def git(self) -> GitStorage:
                return memory_git

        configuration = Configuration(
            server=ServerConfiguration(),
            database=DatabaseConfiguration(url=SecretString("unused")),
            access_token=AccessTokenConfiguration(crypto_key=SecretString(Fernet.generate_key().decode())),
            gitea=GiteaConfiguration(internal_api_url=SecretString(origin), api_token=SecretString("fixture-token")),
        )
        app = make_asgi_application(make_ioc(configuration, Dependencies()))
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            worker = await app.state.dishka_container.get(ManagedWorker)
            assert isinstance(worker.runner, ReviewProjectionRunner)
            assert worker.runner.enabled
            assert worker.task is not None
            scenario, review_id = await published(client, auth_engine)
            async with asyncio.timeout(10):
                while True:
                    response = await client.get(f"/api/v1/reviews/{review_id}", headers=scenario.author)
                    assert response.status_code == 200
                    if response.json()["projection_status"] == "published":
                        break
                    assert response.json()["projection_status"] in {"pending", "retryable_failure"}
                    await asyncio.sleep(0.01)
            assert response.json()["gitea_comment_id"] == 77
            assert response.json()["gitea_review_id"] is None
            assert mock is not None
            assert len(mock.comments) == 1
            assert sum(request.method == "POST" for request in mock.requests) == 1
            assert all("/comments" in request.url.path for request in mock.requests if request.method == "POST")
        assert worker.closed
        assert worker.task.done()
        assert not upstream.is_closed


@asynccontextmanager
async def sql_projection_outage(
    engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    failures: int,
) -> AsyncIterator[None]:
    remaining = failures
    reservation_pending = False

    def database_failure() -> OperationalError:
        cause = (
            SerializationFailure("private serialization details") if stage == "reserve" else OSError("DB unavailable")
        )
        return OperationalError("private SQL", {"token": "private-token"}, cause)

    def fail_statement(_connection: object, _cursor: object, statement: str, *args: object) -> None:
        nonlocal remaining, reservation_pending
        if not remaining:
            return
        if stage == "load" and "review_revisions" in statement:
            remaining -= 1
            raise database_failure()
        if "INSERT INTO" in statement and "review_git_projections" in statement:
            if stage == "reserve":
                remaining -= 1
                raise database_failure()
            if stage == "reserve_committed":
                reservation_pending = True

    original_begin = store.sessions.begin

    @asynccontextmanager
    async def begin_with_commit_failure() -> AsyncIterator[AsyncSession]:
        nonlocal remaining, reservation_pending
        async with original_begin() as session:
            yield session
        if reservation_pending:
            # Model a commit that took effect but whose acknowledgement was lost.
            # The real transaction/session is already committed and closed here.
            reservation_pending = False
            remaining -= 1
            raise database_failure()

    with monkeypatch.context() as patch:
        patch.setattr(store.sessions, "begin", begin_with_commit_failure)
        event.listen(engine.sync_engine, "before_cursor_execute", fail_statement)
        try:
            yield
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", fail_statement)


@pytest.mark.parametrize(
    ("stage", "failures", "final_attempt", "expected_posts", "final_error"),
    [
        pytest.param("load", 2, 3, 1, None, id="load-recovers"),
        pytest.param("load", 5, 5, 0, FailureCode.UNAVAILABLE, id="load-exhausts"),
        pytest.param("reserve", 2, 3, 1, None, id="serialization-recovers"),
        pytest.param("reserve", 5, 5, 0, FailureCode.UNAVAILABLE, id="serialization-exhausts"),
        pytest.param("reserve_committed", 1, 2, 0, None, id="commit-ack-loss-reconciles"),
        pytest.param("reserve_committed", 1, 5, 0, FailureCode.UNCERTAIN, id="commit-ack-loss-exhausts-no-post"),
    ],
)
async def test_transient_sql_load_and_reservation_failures_retry_without_blind_repost(
    learning_client: AsyncClient,
    auth_engine: AsyncEngine,
    store: SqlReviewProjectionStore,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failures: int,
    final_attempt: int,
    expected_posts: int,
    final_error: FailureCode | None,
) -> None:
    await published(learning_client, auth_engine)
    before = await effects(auth_engine)
    storage = MemoryProjectionStorage()
    async with sql_projection_outage(auth_engine, store, monkeypatch, stage=stage, failures=failures):
        runner = ReviewProjectionRunner(store, storage, owner="database-recovery")
        assert await runner.run_once() == RunResult.RETRY_WAIT
        assert storage.posts == 0
        async with auth_engine.connect() as connection:
            row = (await connection.execute(select(store.outbox).where(store.outbox.c.topic == TOPIC))).mappings().one()
            assert row["attempts"] == 1
            assert row["last_error"] == FailureCode.UNAVAILABLE
            receipts = (await connection.execute(select(store.receipts))).mappings().all()
        if stage == "reserve_committed":
            assert len(receipts) == 1
            assert receipts[0]["post_started_at"] is not None
            assert receipts[0]["completed_at"] is None
            if final_error is None:
                item = await store.load(outbox_from_row(row))
                assert item is not None
                storage.comments[item.body] = 77  # Reconcile an independently visible matching remote result.
        else:
            assert receipts == []  # Failed reservations rolled back, so a later first POST is safe.
        for attempt in range(2, final_attempt + 1):
            await age_work(auth_engine)
            result = await runner.run_once()
            if attempt < final_attempt:
                assert result == RunResult.RETRY_WAIT
            else:
                assert result == (RunResult.SUCCEEDED if final_error is None else RunResult.NEEDS_ATTENTION)
        assert await runner.run_once() == RunResult.IDLE
        assert storage.posts == expected_posts
        async with auth_engine.connect() as connection:
            row = (await connection.execute(select(store.outbox).where(store.outbox.c.topic == TOPIC))).mappings().one()
            assert row["attempts"] == final_attempt
            assert row["last_error"] == final_error
        after = await effects(auth_engine)
        assert {key: rows for key, rows in before.items() if key != "outbox_messages"} == {
            key: rows for key, rows in after.items() if key != "outbox_messages"
        }
