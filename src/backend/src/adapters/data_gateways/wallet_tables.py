"""Physical wallet invariants shared with the caller-owned T11 SQL adapter."""

from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from src.adapters.data_gateways.core_tables import enum_column
from src.adapters.data_gateways.ledger_ddl import register_ledger_guard
from src.application.wallet.entity import ReservationState, SourceKind


@dataclass(frozen=True)
class WalletTables:
    guards: Table
    wallets: Table
    ledger: Table
    reservations: Table


def register_wallet_tables(metadata: MetaData) -> WalletTables:
    guards = Table(
        "wallet_owner_guards",
        metadata,
        Column("owner_id", Uuid, ForeignKey("users.id"), primary_key=True),
    )
    wallets = Table(
        "wallets",
        metadata,
        Column("owner_id", Uuid, ForeignKey("wallet_owner_guards.owner_id"), primary_key=True),
        Column("settled", BigInteger, nullable=False),
        Column("reserved", BigInteger, nullable=False),
        Column("karma", BigInteger, nullable=False),
        Column("version", BigInteger, nullable=False),
        CheckConstraint("settled >= 0 AND reserved >= 0 AND reserved <= settled", name="ck_wallet_balances"),
        CheckConstraint("karma >= 0 AND version >= 0", name="ck_wallet_karma_version"),
    )
    ledger = Table(
        "wallet_ledger",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_id", Uuid, ForeignKey("wallets.owner_id"), nullable=False),
        enum_column("source_kind", SourceKind),
        Column("source_subject_id", Uuid, nullable=False),
        Column("source_scope_id", Uuid, ForeignKey("users.id")),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("sequence", BigInteger, nullable=False),
        Column("settled_delta", BigInteger, nullable=False),
        Column("reserved_delta", BigInteger, nullable=False),
        Column("karma_delta", BigInteger, nullable=False),
        Column("related_entry_id", Uuid),
        Column("reservation_id", Uuid),
        Column("job_id", Uuid, ForeignKey("review_jobs.id")),
        Column("assignment_id", Uuid),
        Column("case_id", Uuid, ForeignKey("cases.id")),
        Column("origin_revision_id", Uuid, ForeignKey("review_revisions.id")),
        UniqueConstraint("owner_id", "sequence", name="uq_wallet_ledger_sequence"),
        UniqueConstraint("id", "owner_id", "reservation_id", "job_id", name="uq_wallet_ledger_reservation"),
        CheckConstraint("sequence > 0", name="ck_wallet_ledger_sequence"),
        CheckConstraint(
            "(source_kind = 'SUCCESSFUL_CASE' AND source_scope_id IS NOT NULL AND source_scope_id = owner_id) OR "
            "(source_kind <> 'SUCCESSFUL_CASE' AND source_scope_id IS NULL)",
            name="ck_wallet_source_scope",
        ),
        CheckConstraint(
            "(source_kind = 'WELCOME' AND source_subject_id = owner_id AND settled_delta = 10 "
            "AND reserved_delta = 0 AND karma_delta = 0) OR "
            "(source_kind = 'SUCCESSFUL_CASE' AND case_id IS NOT NULL AND source_subject_id = case_id "
            "AND settled_delta = 3 AND reserved_delta = 0 AND karma_delta = 0) OR "
            "(source_kind = 'REVIEW_REWARD' AND settled_delta = 5 AND reserved_delta = 0 AND karma_delta = 1) OR "
            "(source_kind = 'RESERVE' AND settled_delta = 0 AND reserved_delta = 5 AND karma_delta = 0) OR "
            "(source_kind = 'SETTLE' AND settled_delta = -5 AND reserved_delta = -5 AND karma_delta = 0) OR "
            "(source_kind = 'RELEASE' AND settled_delta = 0 AND reserved_delta = -5 AND karma_delta = 0) OR "
            "(source_kind = 'REFUND' AND settled_delta = 5 AND reserved_delta = 0 AND karma_delta = 0)",
            name="ck_wallet_source_deltas",
        ),
        CheckConstraint(
            "(source_kind = 'WELCOME' AND case_id IS NULL AND origin_revision_id IS NULL "
            "AND reservation_id IS NULL AND job_id IS NULL AND assignment_id IS NULL AND related_entry_id IS NULL) OR "
            "(source_kind IN ('SUCCESSFUL_CASE','REVIEW_REWARD') AND case_id IS NOT NULL "
            "AND origin_revision_id IS NOT NULL AND reservation_id IS NULL AND job_id IS NULL "
            "AND assignment_id IS NULL AND related_entry_id IS NULL) OR "
            "(source_kind IN ('RESERVE','SETTLE','RELEASE','REFUND') AND reservation_id IS NOT NULL "
            "AND source_subject_id = reservation_id AND job_id IS NOT NULL AND case_id IS NULL "
            "AND origin_revision_id IS NULL "
            "AND ((source_kind = 'RESERVE' AND related_entry_id IS NULL AND assignment_id IS NULL) "
            "OR (source_kind = 'RELEASE' AND related_entry_id IS NOT NULL AND assignment_id IS NULL) "
            "OR (source_kind IN ('SETTLE','REFUND') AND related_entry_id IS NOT NULL AND assignment_id IS NOT NULL)))",
            name="ck_wallet_ledger_provenance",
        ),
        ForeignKeyConstraint(
            ["reservation_id", "owner_id", "job_id"],
            ["wallet_reservations.id", "wallet_reservations.owner_id", "wallet_reservations.job_id"],
            name="fk_wallet_ledger_reservation",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["related_entry_id", "owner_id", "reservation_id", "job_id"],
            ["wallet_ledger.id", "wallet_ledger.owner_id", "wallet_ledger.reservation_id", "wallet_ledger.job_id"],
            name="fk_wallet_ledger_related_entry",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "job_id"],
            ["review_assignments.id", "review_assignments.job_id"],
            name="fk_wallet_ledger_assignment_job",
            use_alter=True,
        ),
        Index(
            "uq_wallet_source_unscoped",
            "source_kind",
            "source_subject_id",
            unique=True,
            postgresql_where=text("source_scope_id IS NULL"),
        ),
        Index(
            "uq_wallet_source_scoped",
            "source_kind",
            "source_subject_id",
            "source_scope_id",
            unique=True,
            postgresql_where=text("source_scope_id IS NOT NULL"),
        ),
    )
    reservations = Table(
        "wallet_reservations",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_id", Uuid, ForeignKey("wallets.owner_id"), nullable=False),
        Column("job_id", Uuid, ForeignKey("review_jobs.id"), nullable=False),
        enum_column("state", ReservationState),
        Column("reserve_entry_id", Uuid, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("assignment_id", Uuid),
        Column("settlement_entry_id", Uuid),
        Column("terminal_entry_id", Uuid),
        UniqueConstraint("id", "owner_id", "job_id", name="uq_wallet_reservation_owner_job"),
        UniqueConstraint("reserve_entry_id", name="uq_wallet_reserve_entry"),
        UniqueConstraint("settlement_entry_id", name="uq_wallet_settlement_entry"),
        UniqueConstraint("terminal_entry_id", name="uq_wallet_terminal_entry"),
        CheckConstraint(
            "(state = 'HELD' AND assignment_id IS NULL AND settlement_entry_id IS NULL AND terminal_entry_id IS NULL) "
            "OR (state = 'SETTLED' AND assignment_id IS NOT NULL AND settlement_entry_id IS NOT NULL "
            "AND terminal_entry_id IS NULL) "
            "OR (state = 'RELEASED' AND assignment_id IS NULL AND settlement_entry_id IS NULL "
            "AND terminal_entry_id IS NOT NULL) "
            "OR (state = 'REFUNDED' AND assignment_id IS NOT NULL AND settlement_entry_id IS NOT NULL "
            "AND terminal_entry_id IS NOT NULL)",
            name="ck_wallet_reservation_state",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "job_id"],
            ["review_assignments.id", "review_assignments.job_id"],
            name="fk_wallet_reservation_assignment_job",
            use_alter=True,
        ),
        Index(
            "uq_wallet_active_job",
            "job_id",
            unique=True,
            postgresql_where=text("state IN ('HELD', 'SETTLED')"),
        ),
        Index("ix_wallet_reservation_owner", "owner_id"),
    )
    for field in ("reserve_entry_id", "settlement_entry_id", "terminal_entry_id"):
        reservations.append_constraint(
            ForeignKeyConstraint(
                [field, "owner_id", "id", "job_id"],
                ["wallet_ledger.id", "wallet_ledger.owner_id", "wallet_ledger.reservation_id", "wallet_ledger.job_id"],
                name=f"fk_wallet_reservation_{field}",
            )
        )
    register_ledger_guard(ledger)
    return WalletTables(guards, wallets, ledger, reservations)
