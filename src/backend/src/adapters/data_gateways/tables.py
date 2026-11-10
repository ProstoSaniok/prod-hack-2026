from typing import Final

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, MetaData, String, Table, UniqueConstraint, Uuid
from sqlalchemy.orm import registry

from src.adapters.data_gateways.core_tables import register_core_tables
from src.adapters.data_gateways.development_tables import register_development_tables
from src.adapters.data_gateways.git_learning_tables import register_git_learning_tables
from src.adapters.data_gateways.learning_ddl import register_learning_guard
from src.adapters.data_gateways.learning_tables import extend_projects, register_publication_tables
from src.adapters.data_gateways.operation_tables import register_operation_tables
from src.adapters.data_gateways.priority_tables import register_priority_tables
from src.adapters.data_gateways.project_intent_tables import (
    register_project_file_tables,
    register_project_start_tables,
    register_submission_tables,
)
from src.adapters.data_gateways.review_projection_tables import register_review_projection_tables
from src.adapters.data_gateways.submission_snapshot_tables import extend_submission_snapshots
from src.adapters.data_gateways.wallet_tables import register_wallet_tables
from src.application.access_token.entity import AccessToken
from src.application.auth_identity.entity import AuthIdentity, AuthMethod
from src.application.user.entity import User, UserRole

meta_data: Final = MetaData()
mapper_registry: Final = registry(metadata=meta_data)

user_table: Final = Table(
    "users",
    meta_data,
    Column("id", Uuid, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    Column(
        "role",
        Enum(UserRole, name="user_role", native_enum=False, create_constraint=True),
        nullable=False,
        server_default="USER",
    ),
)

auth_identity_table: Final = Table(
    "auth_identities",
    meta_data,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    Column("method", Enum(AuthMethod, name="auth_method", native_enum=False, create_constraint=True), nullable=False),
    Column("identifier", String(320), nullable=False),
    Column("secret_key", String, nullable=False),
    UniqueConstraint("method", "identifier", name="uq_auth_method_identifier"),
)

access_token_table: Final = Table(
    "access_token",
    meta_data,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    Column("expires_in", DateTime(timezone=True), nullable=False),
    Column("revoked", Boolean, nullable=False),
)

mapper_registry.map_imperatively(User, user_table)
mapper_registry.map_imperatively(AuthIdentity, auth_identity_table)
mapper_registry.map_imperatively(AccessToken, access_token_table)

register_core_tables(meta_data, mapper_registry)
register_development_tables(meta_data, mapper_registry)

_operations: Final = register_operation_tables(meta_data)
request_scopes_table: Final = _operations.scopes
request_generations_table: Final = _operations.generations
request_operation_bindings_table: Final = _operations.bindings
operations_table: Final = _operations.operations
register_git_learning_tables(meta_data)
outbox_messages_table: Final = _operations.outbox
projection_receipts_table: Final = _operations.receipts

_wallet: Final = register_wallet_tables(meta_data)
wallet_owner_guards_table: Final = _wallet.guards
wallets_table: Final = _wallet.wallets
wallet_ledger_table: Final = _wallet.ledger
wallet_reservations_table: Final = _wallet.reservations
register_priority_tables(meta_data)

case_content_versions_table, case_content_competencies_table = register_publication_tables(meta_data, mapper_registry)
extend_projects(meta_data)
project_start_intents_table, project_start_results_table, project_mutation_guards_table = register_project_start_tables(
    meta_data
)
project_file_intents_table, project_file_results_table = register_project_file_tables(meta_data)
submission_intents_table, submission_results_table, submission_git_bindings_table = register_submission_tables(
    meta_data
)
extend_submission_snapshots(meta_data)
register_review_projection_tables(meta_data)
for _evidence_table in (
    case_content_versions_table,
    case_content_competencies_table,
    project_start_intents_table,
    project_start_results_table,
    project_file_intents_table,
    project_file_results_table,
    submission_intents_table,
    submission_results_table,
    submission_git_bindings_table,
    meta_data.tables["submission_competencies"],
    meta_data.tables["git_learning_jobs"],
    meta_data.tables["git_learning_results"],
):
    register_learning_guard(_evidence_table)
