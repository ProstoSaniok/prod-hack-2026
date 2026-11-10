from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.autogenerate.api import AutogenContext
from alembic.operations.ops import UpgradeOps
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from src.adapters.data_gateways.tables import (
    meta_data,
    operations_table,
    outbox_messages_table,
    projection_receipts_table,
    request_generations_table,
    request_operation_bindings_table,
    request_scopes_table,
    wallet_ledger_table,
    wallet_owner_guards_table,
    wallet_reservations_table,
    wallets_table,
)
from src.application.operations.work import WorkState
from src.application.wallet.entity import ReservationState, SourceKind
from src.migrations.persistence_autogenerate import compare_persistence_ddl
from tests.persistence.helpers import insert, seed_review, seed_subject


def test_request_storage_keeps_exact_response_and_historical_binding(core_engine: Engine) -> None:
    now = datetime.now(UTC)
    scope, first, second, operation = (uuid4() for _ in range(4))
    body, location = b"x" * 1048576, "/" + "x" * 8191
    with core_engine.begin() as connection:
        actor = insert(connection, "users", role="USER")
        connection.execute(
            operations_table.insert().values(
                id=operation,
                kind="upload",
                source_key="s" * 256,
                subject_id=actor,
                payload_hash="a" * 64,
                external_id="e" * 256,
                created_at=now,
                available_at=now,
                state=WorkState.PENDING,
                attempts=0,
            )
        )
        connection.execute(
            outbox_messages_table.insert().values(
                id=uuid4(),
                topic="published",
                source_key="s" * 256,
                subject_id=actor,
                revision=1,
                created_at=now,
                available_at=now,
                state=WorkState.PENDING,
                attempts=0,
            )
        )
        connection.execute(
            projection_receipts_table.insert().values(
                consumer="profile",
                topic="published",
                source_key="s" * 256,
                subject_id=actor,
                revision=1,
            )
        )
        connection.execute(
            request_scopes_table.insert().values(
                id=scope,
                actor_id=actor,
                method="POST",
                path="/submissions",
                request_key="request-1",
            )
        )
        connection.execute(
            request_generations_table.insert().values(
                id=first,
                scope_id=scope,
                payload_hash="a" * 64,
                created_at=now,
                expires_at=now + timedelta(days=1),
                response_status=201,
                response_body=body,
                response_location=location,
                response_queue_header_name="x-QuEuE-StAtE",
                response_queue_header_value="waiting",
            )
        )
        connection.execute(
            request_operation_bindings_table.insert().values(request_id=first, operation_id=operation, created_at=now)
        )
        connection.execute(
            request_generations_table.insert().values(
                id=second,
                scope_id=scope,
                payload_hash="a" * 64,
                created_at=now + timedelta(days=1),
                expires_at=now + timedelta(days=2),
            )
        )
        connection.execute(
            request_scopes_table.update().where(request_scopes_table.c.id == scope).values(current_request_id=second)
        )
    with core_engine.begin() as connection:
        saved = connection.execute(select(request_generations_table).where(request_generations_table.c.id == first))
        row = saved.mappings().one()
        assert (row["response_status"], row["response_body"], row["response_location"]) == (201, body, location)
        assert (row["response_queue_header_name"], row["response_queue_header_value"]) == ("x-QuEuE-StAtE", "waiting")
        assert connection.scalar(select(request_operation_bindings_table.c.operation_id)) == operation
        assert connection.scalar(select(request_scopes_table.c.current_request_id)) == second
        with pytest.raises(IntegrityError, match="ck_request_response_size"), connection.begin_nested():
            connection.execute(
                request_generations_table.update()
                .where(request_generations_table.c.id == first)
                .values(response_body=body + b"x")
            )


def test_wallet_deferred_cycle_global_source_and_atomic_rollback(core_engine: Engine) -> None:
    now, reservation, reserve_entry, reward = datetime.now(UTC), uuid4(), uuid4(), uuid4()
    with core_engine.begin() as connection:
        subject = seed_subject(connection)
        review, revision = seed_review(connection, subject)
        for owner in (subject.reviewer, subject.author):
            connection.execute(wallet_owner_guards_table.insert().values(owner_id=owner))
            connection.execute(
                wallets_table.insert().values(owner_id=owner, settled=10, reserved=0, karma=0, version=0)
            )
        connection.execute(
            wallet_ledger_table.insert().values(
                id=reward,
                owner_id=subject.reviewer,
                source_kind=SourceKind.REVIEW_REWARD,
                source_subject_id=review,
                created_at=now,
                sequence=1,
                settled_delta=5,
                reserved_delta=0,
                karma_delta=1,
                case_id=subject.case,
                origin_revision_id=revision,
            )
        )
        connection.execute(
            wallet_ledger_table.insert().values(
                id=reserve_entry,
                owner_id=subject.author,
                source_kind=SourceKind.RESERVE,
                source_subject_id=reservation,
                created_at=now,
                sequence=1,
                settled_delta=0,
                reserved_delta=5,
                karma_delta=0,
                reservation_id=reservation,
                job_id=subject.job,
            )
        )
        connection.execute(
            wallet_reservations_table.insert().values(
                id=reservation,
                owner_id=subject.author,
                job_id=subject.job,
                state=ReservationState.HELD,
                reserve_entry_id=reserve_entry,
                created_at=now,
            )
        )
        connection.execute(text("SET CONSTRAINTS fk_wallet_ledger_reservation IMMEDIATE"))

    def conflicting_payment() -> None:
        with core_engine.begin() as connection:
            connection.execute(
                wallets_table.update().where(wallets_table.c.owner_id == subject.author).values(settled=15)
            )
            connection.execute(
                wallet_ledger_table.insert().values(
                    id=uuid4(),
                    owner_id=subject.author,
                    source_kind=SourceKind.REVIEW_REWARD,
                    source_subject_id=review,
                    created_at=now,
                    sequence=2,
                    settled_delta=5,
                    reserved_delta=0,
                    karma_delta=1,
                    case_id=subject.case,
                    origin_revision_id=revision,
                )
            )

    with pytest.raises(IntegrityError, match="uq_wallet_source_unscoped"):
        conflicting_payment()
    with core_engine.connect() as connection:
        assert (
            connection.scalar(select(wallets_table.c.settled).where(wallets_table.c.owner_id == subject.author)) == 10
        )
        assert (
            connection.scalar(select(wallet_ledger_table.c.owner_id).where(wallet_ledger_table.c.id == reward))
            == subject.reviewer
        )
        assert connection.scalar(select(wallet_reservations_table.c.state)) == ReservationState.HELD


def test_ledger_mutation_and_trigger_drift_are_rejected(core_engine: Engine) -> None:
    for statement in (
        "UPDATE wallet_ledger SET sequence = sequence",
        "DELETE FROM wallet_ledger",
        "TRUNCATE wallet_ledger CASCADE",
    ):
        with pytest.raises(DBAPIError) as caught, core_engine.begin() as connection:
            connection.execute(text(statement))
        assert getattr(caught.value.orig, "sqlstate", None) == "55000"
    # Each tamper is rolled back; both conditional and column-limited guards
    # would otherwise leave paths that mutate supposedly immutable history.
    for event, condition in (("UPDATE OR DELETE OR TRUNCATE", "WHEN (false)"), ("UPDATE OF sequence", "")):
        with core_engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text("DROP TRIGGER prodguild_wallet_ledger_immutable ON wallet_ledger"))
            connection.execute(
                text(
                    f"CREATE TRIGGER prodguild_wallet_ledger_immutable BEFORE {event} ON wallet_ledger "
                    f"FOR EACH STATEMENT {condition} EXECUTE FUNCTION prodguild_reject_ledger_mutation()"
                )
            )
            with pytest.raises(ValueError, match="differs from metadata"):
                compare_metadata(MigrationContext.configure(connection), meta_data)
            transaction.rollback()


def test_ledger_guard_uses_translated_schema_for_create_and_drop(core_engine: Engine) -> None:
    schema = f't02_translated_{uuid4().hex}"quoted'
    engine = create_engine(core_engine.url, execution_options={"schema_translate_map": {None: schema}})
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
            meta_data.create_all(connection)
        with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
            connection.execute(wallet_ledger_table.delete())
        assert getattr(caught.value.orig, "sqlstate", None) == "55000"
        with engine.begin() as connection:
            # Exercise the custom comparator with the same translated mapping,
            # independently of Alembic's default-schema table reflection.
            compare_persistence_ddl(
                AutogenContext(MigrationContext.configure(connection), metadata=meta_data), UpgradeOps([]), {schema}
            )
            meta_data.drop_all(connection)
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                        "WHERE n.nspname=:schema AND p.proname='prodguild_reject_ledger_mutation'"
                    ),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        engine.dispose()
