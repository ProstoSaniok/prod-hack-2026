from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
)

from src.adapters.data_gateways.learning_ddl import register_learning_guard


def register_priority_tables(metadata: MetaData) -> None:
    Table(
        "priority_requests",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("reservation_id", Uuid, nullable=False, unique=True),
        Column("owner_id", Uuid, nullable=False),
        Column("job_id", Uuid, nullable=False),
        Column("submission_id", Uuid, ForeignKey("submissions.id"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("cost", Integer, nullable=False),
        Column("policy_version", String(100), nullable=False),
        ForeignKeyConstraint(
            ["reservation_id", "owner_id", "job_id"],
            ["wallet_reservations.id", "wallet_reservations.owner_id", "wallet_reservations.job_id"],
            name="fk_priority_reservation",
        ),
        CheckConstraint("cost = 5", name="ck_priority_cost"),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_priority_policy"),
    )
    Table(
        "priority_scheduler",
        metadata,
        Column("name", String(32), primary_key=True),
        Column("consecutive_priority", Integer, nullable=False),
        CheckConstraint("name = 'primary' AND consecutive_priority BETWEEN 0 AND 3", name="ck_priority_scheduler"),
    )
    register_learning_guard(metadata.tables["priority_requests"])
