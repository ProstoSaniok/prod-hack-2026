"""Durable exact case Git input and request-driven recovery state."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, LargeBinary, MetaData, String, Table, Uuid


def register_git_learning_tables(metadata: MetaData) -> None:
    Table(
        "git_learning_jobs",
        metadata,
        Column("operation_id", Uuid, ForeignKey("operations.id"), primary_key=True),
        Column("actor_id", Uuid, ForeignKey("users.id"), nullable=False),
        Column("resource_id", Uuid, nullable=False),
        Column("input", LargeBinary, nullable=False),
        Column("input_sha256", String(64), nullable=False),
        CheckConstraint(
            "octet_length(input) BETWEEN 1 AND 8388608 AND input_sha256 = encode(sha256(input), 'hex')",
            name="ck_git_job_input",
        ),
    )
    Table(
        "git_learning_results",
        metadata,
        Column("operation_id", Uuid, ForeignKey("git_learning_jobs.operation_id"), primary_key=True),
        Column("response", LargeBinary, nullable=False),
        Column("response_status", Integer, nullable=False),
        Column("response_location", String(256)),
        CheckConstraint(
            "response_status IN (200, 201, 204) AND octet_length(response) <= 262144", name="ck_git_job_response"
        ),
    )
    Table(
        "git_learning_guards",
        metadata,
        Column("resource_id", Uuid, primary_key=True),
        Column("operation_id", Uuid, ForeignKey("git_learning_jobs.operation_id"), unique=True),
    )
    Table(
        "git_workspaces",
        metadata,
        Column("repository_id", Uuid, ForeignKey("repositories.id"), primary_key=True),
        Column("name", String(100), nullable=False, unique=True),
        Column("service_owner_id", Integer, nullable=False),
        Column("provider_instance_key", String(64), nullable=False),
        Column("head_sha", String(40), nullable=False),
        Column("tree_sha", String(40), nullable=False),
        CheckConstraint(
            "head_sha ~ '^[0-9a-f]{40}$' AND tree_sha ~ '^[0-9a-f]{40}$' AND service_owner_id > 0",
            name="ck_git_workspace_identity",
        ),
    )
