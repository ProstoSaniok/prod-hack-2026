"""Topic-isolated durable runner store; every method owns and closes its session."""

from collections.abc import Callable, Coroutine
from dataclasses import replace
from decimal import Decimal
from functools import wraps
from uuid import UUID, uuid4

from sqlalchemy import MetaData, and_, case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlTimeoutError,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.adapters.operations.sql_records import optional, outbox_from_row, value, work_values
from src.adapters.operations.sql_work import SqlOutbox, database_now
from src.application.catalog.entity import RepositoryPurpose
from src.application.operations.entity import OutboxMessage
from src.application.operations.recovery import DependencyError, RunResult, result_for
from src.application.operations.work import FailureCode, LeaseLostError, RetryPolicy, WorkState
from src.application.review.entity import DiffSide, ReviewState, ReviewVerdict, RevisionContent
from src.application.review.projection import TOPIC, ProjectionComment, PublishedReviewProjection


def _database_errors[**P, T](
    operation: Callable[P, Coroutine[object, object, T]],
) -> Callable[P, Coroutine[object, object, T]]:
    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        code = FailureCode.UNAVAILABLE
        try:
            return await operation(*args, **kwargs)
        except DBAPIError as failure:
            # PostgreSQL connection failures, serialization failures and deadlocks
            # are OperationalError/InterfaceError. Constraint/programming errors
            # stay permanent; do not disguise broken invariants as outages.
            if not isinstance(failure, (OperationalError, InterfaceError)) and not failure.connection_invalidated:
                code = FailureCode.INTERNAL
        except (DisconnectionError, SqlTimeoutError):
            pass
        # Raise after session cleanup and outside the handler: private SQL,
        # parameters and driver exception context must never cross the port.
        # A failed commit acknowledgement may hide a committed POST intention;
        # retries reconcile it, never delete it or blindly send another POST.
        raise DependencyError(code)

    return run


class SqlReviewProjectionStore:
    def __init__(self, engine: AsyncEngine, instance_key: str, metadata: MetaData) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        self.instance_key, self.metadata = instance_key, metadata
        self.outbox = metadata.tables["outbox_messages"]
        self.receipts = metadata.tables["review_git_projections"]

    @_database_errors
    async def claim(self, *, owner: str, policy: RetryPolicy) -> OutboxMessage | None:
        table = self.outbox
        async with self.sessions.begin() as session:
            for _ in range(100):
                now = await database_now(session)
                due = or_(
                    and_(table.c.state.in_((WorkState.PENDING, WorkState.RETRY_WAIT)), table.c.available_at <= now),
                    and_(table.c.state == WorkState.RUNNING, table.c.lease_until <= now),
                )
                effective = case((table.c.state == WorkState.RUNNING, table.c.lease_until), else_=table.c.available_at)
                row = (
                    (
                        await session.execute(
                            select(table)
                            .where(table.c.topic == TOPIC, due)
                            .order_by(effective, table.c.id)
                            .limit(1)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return None
                message = outbox_from_row(row)
                work = message.work.claim(now=await database_now(session), owner=owner, token=uuid4(), policy=policy)
                await session.execute(update(table).where(table.c.id == message.id).values(**work_values(work)))
                if work.state == WorkState.RUNNING:
                    return replace(message, work=work)
        return None

    @_database_errors
    async def load(self, message: OutboxMessage) -> PublishedReviewProjection | None:
        if message.spec.topic != TOPIC:
            return None
        try:
            revision_id = UUID(message.spec.source_key)
        except ValueError:
            return None
        if str(revision_id) != message.spec.source_key:
            return None
        tables = self.metadata.tables
        reviews, revisions, submissions, projects, repositories, workspaces, results, bindings = (
            tables[name]
            for name in (
                "review_results",
                "review_revisions",
                "submissions",
                "projects",
                "repositories",
                "git_workspaces",
                "submission_results",
                "submission_git_bindings",
            )
        )
        base, head = tables["retained_references"].alias("base"), tables["retained_references"].alias("head")
        statement = (
            select(
                revisions,
                submissions.c.id.label("target_submission_id"),
                projects.c.id.label("target_project_id"),
                repositories.c.external_id.label("repository_external_id"),
                workspaces.c.name.label("repository_name"),
                workspaces.c.service_owner_id,
                results.c.external_repository_id,
                results.c.external_pr_id,
                results.c.pr_number,
                base.c.external_id.label("base_ref"),
                base.c.commit_id.label("base_sha"),
                head.c.external_id.label("head_ref"),
                head.c.commit_id.label("head_sha"),
            )
            .select_from(reviews)
            .join(
                revisions,
                and_(revisions.c.id == reviews.c.published_revision_id, revisions.c.review_id == reviews.c.id),
            )
            .join(submissions, submissions.c.id == reviews.c.submission_id)
            .join(
                projects,
                and_(
                    projects.c.id == submissions.c.project_id, projects.c.repository_id == submissions.c.repository_id
                ),
            )
            .join(repositories, repositories.c.id == submissions.c.repository_id)
            .join(workspaces, workspaces.c.repository_id == repositories.c.id)
            .join(
                bindings,
                and_(bindings.c.submission_id == submissions.c.id, bindings.c.repository_id == repositories.c.id),
            )
            .join(
                results,
                and_(
                    results.c.operation_id == bindings.c.operation_id,
                    results.c.operation_id == submissions.c.operation_id,
                ),
            )
            .join(
                base,
                and_(base.c.id == submissions.c.base_retained_reference_id, base.c.repository_id == repositories.c.id),
            )
            .join(
                head, and_(head.c.id == submissions.c.retained_reference_id, head.c.repository_id == repositories.c.id)
            )
            .where(
                reviews.c.id == message.spec.subject_id,
                reviews.c.state == ReviewState.PUBLISHED,
                reviews.c.published_at.is_not(None),
                revisions.c.id == revision_id,
                revisions.c.number == message.spec.revision,
                revisions.c.submission_id == submissions.c.id,
                projects.c.participant_id == submissions.c.author_id,
                repositories.c.owner_id == submissions.c.author_id,
                repositories.c.provider == "gitea",
                repositories.c.purpose == RepositoryPurpose.WORKSPACE,
                workspaces.c.provider_instance_key == self.instance_key,
                results.c.provider_instance_key == self.instance_key,
                results.c.submission_id == submissions.c.id,
                results.c.repository_id == repositories.c.id,
                results.c.base_retained_reference_id == base.c.id,
                results.c.retained_reference_id == head.c.id,
                results.c.commit_sha == head.c.commit_id,
                results.c.head_ref == head.c.external_id,
                *(
                    table.c.deleted_at.is_(None)
                    for table in (reviews, revisions, submissions, projects, repositories, base, head)
                ),
            )
        )
        async with self.sessions() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
            if row is None:
                return None
            project_id, submission_id = value(row, "target_project_id", UUID), value(row, "target_submission_id", UUID)
            if (
                row["repository_external_id"] != str(row["external_repository_id"])
                or row["repository_name"] != f"prodguild-project-{project_id.hex}"
                or row["base_ref"] != f"refs/heads/prodguild/bases/{project_id}"
                or row["head_ref"] != f"refs/heads/prodguild/submissions/{submission_id}"
            ):
                return None
            comments = tables["review_comments"]
            comment_rows = (
                (
                    await session.execute(
                        select(comments)
                        .where(
                            comments.c.revision_id == revision_id,
                            comments.c.draft_id.is_(None),
                            comments.c.deleted_at.is_(None),
                        )
                        .order_by(comments.c.created_at, comments.c.id)
                    )
                )
                .mappings()
                .all()
            )
            return PublishedReviewProjection(
                review_id=message.spec.subject_id,
                revision_id=revision_id,
                revision_number=message.spec.revision,
                repository_name=value(row, "repository_name", str),
                repository_id=value(row, "external_repository_id", int),
                service_owner_id=value(row, "service_owner_id", int),
                pull_number=value(row, "pr_number", int),
                pull_id=value(row, "external_pr_id", int),
                pull_marker=f"prodguild-submission:{submission_id}",
                base_branch=value(row, "base_ref", str).removeprefix("refs/heads/"),
                base_sha=value(row, "base_sha", str),
                head_branch=value(row, "head_ref", str).removeprefix("refs/heads/"),
                head_sha=value(row, "head_sha", str),
                content=RevisionContent(
                    score=value(row, "score", Decimal),
                    verdict=value(row, "verdict", ReviewVerdict),
                    strengths=value(row, "strengths", str),
                    problems=value(row, "problems", str),
                    next_steps=value(row, "next_steps", str),
                ),
                comments=tuple(
                    ProjectionComment(
                        text=value(item, "text", str),
                        path=optional(item, "path", str),
                        side=optional(item, "side", DiffSide),
                        line=optional(item, "line", int),
                        end_line=optional(item, "end_line", int),
                    )
                    for item in comment_rows
                ),
            )

    async def _leased(self, session: AsyncSession, message: OutboxMessage) -> OutboxMessage | None:
        row = (
            (await session.execute(select(self.outbox).where(self.outbox.c.id == message.id).with_for_update()))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        current = outbox_from_row(row)
        lease, expected = current.work.lease, message.work.lease
        if (
            current.spec != message.spec
            or lease is None
            or expected is None
            or lease.token != expected.token
            or lease.until <= await database_now(session)
        ):
            return None
        return current

    @staticmethod
    def _identity(message: OutboxMessage, projection: PublishedReviewProjection) -> dict[str, object]:
        if (
            message.spec.topic != TOPIC
            or message.spec.subject_id != projection.review_id
            or message.spec.source_key != str(projection.revision_id)
            or message.spec.revision != projection.revision_number
        ):
            raise ValueError("Projection identity changed")
        return {
            "message_id": message.id,
            "review_id": projection.review_id,
            "revision_id": projection.revision_id,
            "revision_number": projection.revision_number,
            "body_hash": projection.body_hash,
        }

    @_database_errors
    async def reserve_post(self, message: OutboxMessage, projection: PublishedReviewProjection) -> bool:
        async with self.sessions.begin() as session:
            if await self._leased(session, message) is None:
                return False
            now = await database_now(session)
            inserted = await session.execute(
                insert(self.receipts)
                .values(**self._identity(message, projection), created_at=now, post_started_at=now)
                .on_conflict_do_nothing(index_elements=[self.receipts.c.message_id])
                .returning(self.receipts.c.message_id)
            )
            return inserted.scalar_one_or_none() is not None

    @_database_errors
    async def finish(
        self,
        message: OutboxMessage,
        *,
        projection: PublishedReviewProjection | None,
        external_id: int | None,
        error: FailureCode | None,
        policy: RetryPolicy,
    ) -> RunResult:
        async with self.sessions.begin() as session:
            current = await self._leased(session, message)
            if current is None or current.work.lease is None:
                return RunResult.LEASE_LOST
            now, token = await database_now(session), current.work.lease.token
            try:
                work = (
                    current.work.succeed(token=token, now=now)
                    if error is None
                    else current.work.fail(token=token, now=now, code=error, policy=policy)
                )
            except LeaseLostError:
                return RunResult.LEASE_LOST
            if not await SqlOutbox(session, self.outbox).save_leased(replace(current, work=work), token=token, now=now):
                return RunResult.LEASE_LOST
            if error is None:
                if projection is None or type(external_id) is not int or external_id < 1:
                    raise ValueError("Successful projection requires a remote receipt")
                identity = self._identity(message, projection)
                await session.execute(
                    insert(self.receipts)
                    .values(**identity, created_at=now)
                    .on_conflict_do_nothing(index_elements=[self.receipts.c.message_id])
                )
                row = (
                    (await session.execute(select(self.receipts).where(self.receipts.c.message_id == message.id)))
                    .mappings()
                    .one()
                )
                if any(row[key] != expected for key, expected in identity.items()):
                    raise ValueError("Projection receipt identity changed")
                if row["external_comment_id"] not in {None, external_id}:
                    raise ValueError("Projection receipt remote identity changed")
                await session.execute(
                    update(self.receipts)
                    .where(self.receipts.c.message_id == message.id)
                    .values(
                        external_comment_id=external_id,
                        completed_at=now,
                    )
                )
            return result_for(work)
