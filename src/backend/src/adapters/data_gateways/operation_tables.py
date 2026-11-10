"""Shared operation metadata; immutable application DTOs use Core serializers."""

from dataclasses import dataclass

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from src.adapters.data_gateways.core_tables import enum_column
from src.application.operations.work import FailureCode, WorkState


@dataclass(frozen=True)
class OperationTables:
    scopes: Table
    generations: Table
    bindings: Table
    operations: Table
    outbox: Table
    receipts: Table


def _work(table: Table) -> None:
    table.append_column(Column("created_at", DateTime(timezone=True), nullable=False))
    table.append_column(Column("available_at", DateTime(timezone=True), nullable=False))
    table.append_column(enum_column("state", WorkState))
    table.append_column(Column("attempts", Integer, nullable=False))
    table.append_column(Column("lease_token", Uuid))
    table.append_column(Column("lease_owner", String(128)))
    table.append_column(Column("lease_until", DateTime(timezone=True)))
    table.append_column(enum_column("last_error", FailureCode, nullable=True))
    table.append_constraint(CheckConstraint("attempts >= 0", name=f"ck_{table.name}_attempts"))
    table.append_constraint(
        CheckConstraint(
            "(state = 'RUNNING' AND lease_token IS NOT NULL AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND length(lease_owner) > 0) OR "
            "(state <> 'RUNNING' AND lease_token IS NULL AND lease_owner IS NULL AND lease_until IS NULL)",
            name=f"ck_{table.name}_lease",
        )
    )
    Index(
        f"ix_{table.name}_due",
        table.c.available_at,
        table.c.id,
        postgresql_where=text("state IN ('PENDING', 'RETRY_WAIT')"),
    )
    Index(
        f"ix_{table.name}_expired",
        table.c.lease_until,
        table.c.id,
        postgresql_where=text("state = 'RUNNING'"),
    )


def register_operation_tables(metadata: MetaData) -> OperationTables:
    operations = Table(
        "operations",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("kind", String(96), nullable=False),
        Column("source_key", String(256), nullable=False),
        Column("subject_id", Uuid, nullable=False),
        Column("payload_hash", String(64), nullable=False),
        Column("external_id", String(256)),
        UniqueConstraint("kind", "source_key", name="uq_operation_source"),
        CheckConstraint("kind ~ '^[a-z][a-z0-9_.-]{0,95}$'", name="ck_operation_kind"),
        CheckConstraint("source_key ~ '^[!-~]+$'", name="ck_operation_source"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_operation_payload_hash"),
        CheckConstraint("external_id ~ '^[A-Za-z0-9_.:-]+$'", name="ck_operation_external_id"),
    )
    _work(operations)
    scopes = Table(
        "request_scopes",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("actor_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("method", String(6), nullable=False),
        Column("path", String(2048), nullable=False),
        Column("request_key", String(128), nullable=False),
        Column("current_request_id", Uuid),
        UniqueConstraint("actor_id", "method", "path", "request_key", name="uq_request_scope"),
        CheckConstraint("method IN ('POST', 'PUT', 'PATCH', 'DELETE')", name="ck_request_method"),
        CheckConstraint("request_key ~ '^[A-Za-z0-9._~-]{1,128}$'", name="ck_request_key"),
        CheckConstraint("path ~ '^/[A-Za-z0-9._~/-]*$'", name="ck_request_path"),
        ForeignKeyConstraint(
            ["current_request_id", "id"],
            ["request_generations.id", "request_generations.scope_id"],
            name="fk_request_scope_current_generation",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    generations = Table(
        "request_generations",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("scope_id", Uuid, ForeignKey("request_scopes.id"), nullable=False),
        Column("payload_hash", String(64), nullable=False),
        Column("canonical_payload_version", String(64), nullable=False, server_default="prodguild-json-v1"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("response_status", Integer),
        Column("response_body", LargeBinary),
        Column("response_location", String(8192)),
        Column("response_queue_header_name", String(64)),
        Column("response_queue_header_value", String(128)),
        UniqueConstraint("id", "scope_id", name="uq_request_generation_scope"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_request_payload_hash"),
        CheckConstraint("length(canonical_payload_version) > 0", name="ck_request_payload_version"),
        CheckConstraint("expires_at >= created_at + interval '24 hours'", name="ck_request_retention"),
        CheckConstraint(
            "(response_status IS NULL AND response_body IS NULL AND response_location IS NULL "
            "AND response_queue_header_name IS NULL AND response_queue_header_value IS NULL) OR "
            "(response_status IS NOT NULL AND response_body IS NOT NULL)",
            name="ck_request_response_presence",
        ),
        CheckConstraint(
            "response_status >= 200 AND response_status < 500 "
            "AND response_status NOT IN (206,304,401,403,407,408,425,429)",
            name="ck_request_response_status",
        ),
        CheckConstraint("octet_length(response_body) <= 1048576", name="ck_request_response_size"),
        CheckConstraint(
            "response_status NOT IN (204,205) OR octet_length(response_body) = 0",
            name="ck_request_empty_response",
        ),
        CheckConstraint("response_location ~ '^[!-~]+$'", name="ck_request_location"),
        CheckConstraint(
            "(response_queue_header_name IS NULL AND response_queue_header_value IS NULL) OR "
            "(response_queue_header_name IS NOT NULL AND response_queue_header_value IS NOT NULL "
            "AND lower(response_queue_header_name) = 'x-queue-state' "
            "AND response_queue_header_value ~ '^[a-z][a-z0-9_-]{0,31}$')",
            name="ck_request_queue_header",
        ),
        Index("ix_request_generation_expiry", "expires_at"),
        Index("ix_request_generation_scope", "scope_id"),
    )
    bindings = Table(
        "request_operation_bindings",
        metadata,
        Column("request_id", Uuid, ForeignKey("request_generations.id"), primary_key=True),
        Column("operation_id", Uuid, ForeignKey("operations.id"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index("ix_request_operation_binding", "operation_id", "request_id"),
    )
    outbox = Table(
        "outbox_messages",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("topic", String(96), nullable=False),
        Column("source_key", String(256), nullable=False),
        Column("subject_id", Uuid, nullable=False),
        Column("revision", Integer, nullable=False),
        UniqueConstraint("topic", "source_key", name="uq_outbox_source"),
        CheckConstraint("topic ~ '^[a-z][a-z0-9_.-]{0,95}$'", name="ck_outbox_topic"),
        CheckConstraint("source_key ~ '^[!-~]+$'", name="ck_outbox_source"),
        CheckConstraint("revision > 0", name="ck_outbox_revision"),
    )
    _work(outbox)
    receipts = Table(
        "projection_receipts",
        metadata,
        Column("consumer", String(96), primary_key=True),
        Column("topic", String(96), primary_key=True),
        Column("source_key", String(256), primary_key=True),
        Column("subject_id", Uuid, nullable=False),
        Column("revision", Integer, nullable=False),
        CheckConstraint("consumer ~ '^[a-z][a-z0-9_.-]{0,95}$'", name="ck_projection_consumer"),
        CheckConstraint("topic ~ '^[a-z][a-z0-9_.-]{0,95}$'", name="ck_projection_topic"),
        CheckConstraint("source_key ~ '^[!-~]+$'", name="ck_projection_source"),
        CheckConstraint("revision > 0", name="ck_projection_revision"),
    )
    return OperationTables(scopes, generations, bindings, operations, outbox, receipts)
