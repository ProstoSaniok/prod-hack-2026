"""Operation-owned typed recovery facts. HTTP expiry never owns these rows."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

from src.adapters.data_gateways.learning_tables import bind, check, sha


def _intent(metadata: MetaData, name: str) -> Table:
    table = Table(
        name,
        metadata,
        Column("operation_id", Uuid, ForeignKey("operations.id"), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("payload_namespace", String(64), nullable=False),
        Column("payload_hash", String(64), nullable=False),
    )
    check(table, f"ck_{name}_payload", "length(payload_namespace) > 0 AND payload_hash ~ '^[0-9a-f]{64}$'")
    bind(table, f"fk_{name}_payload", ("operation_id", "payload_hash"), "operations", ("id", "payload_hash"))
    return table


def _result(metadata: MetaData, name: str, intent: str) -> Table:
    return Table(
        name,
        metadata,
        Column("operation_id", Uuid, ForeignKey(f"{intent}.operation_id"), primary_key=True),
        Column("observed_at", DateTime(timezone=True), nullable=False),
    )


def _external_id(table: Table, name: str) -> None:
    table.append_column(Column(name, BigInteger, nullable=False))
    check(table, f"ck_{table.name}_{name}", f"{name} > 0")


def _project_target(table: Table) -> None:
    for name in ("actor_id", "project_id", "repository_id"):
        table.append_column(Column(name, Uuid, nullable=False))
    bind(
        table,
        f"fk_{table.name}_target",
        ("project_id", "repository_id", "actor_id"),
        "projects",
        ("id", "repository_id", "participant_id"),
    )


def register_project_start_tables(metadata: MetaData) -> tuple[Table, Table, Table]:
    metadata.tables["operations"].append_constraint(UniqueConstraint("id", "payload_hash", name="uq_operation_payload"))
    intent = _intent(metadata, "project_start_intents")
    for name in ("reserved_project_id", "reserved_repository_id"):
        intent.append_column(Column(name, Uuid, nullable=False, unique=True))
    for name in ("actor_id", "participant_id"):
        intent.append_column(Column(name, Uuid, ForeignKey("users.id"), nullable=False))
    for name in ("case_id", "content_version_id", "starter_repository_id", "starter_retained_reference_id"):
        intent.append_column(Column(name, Uuid, nullable=False))
    intent.append_column(Column("content_version", Integer, nullable=False))
    intent.append_column(Column("provider_instance_key", String(100), nullable=False))
    _external_id(intent, "service_owner_id")
    intent.append_column(Column("repository_slug", String(50), nullable=False))
    intent.append_column(Column("working_branch", String(4), nullable=False))
    sha(intent, "starter_commit_sha")
    sha(intent, "source_tree_sha")
    intent.append_constraint(UniqueConstraint("participant_id", "case_id", name="uq_start_participant_case"))
    intent.append_constraint(
        UniqueConstraint(
            "operation_id",
            "reserved_project_id",
            "reserved_repository_id",
            "participant_id",
            "case_id",
            "content_version_id",
            "content_version",
            name="uq_start_project_binding",
        )
    )
    intent.append_constraint(
        UniqueConstraint("operation_id", "reserved_project_id", "service_owner_id", name="uq_start_result")
    )
    bind(
        intent,
        "fk_start_content",
        ("content_version_id", "case_id", "content_version"),
        "case_content_versions",
        ("id", "case_id", "content_version"),
    )
    bind(
        intent,
        "fk_start_starter",
        (
            "content_version_id",
            "starter_repository_id",
            "starter_retained_reference_id",
            "starter_commit_sha",
            "source_tree_sha",
        ),
        "case_content_versions",
        ("id", "published_repository_id", "published_retained_reference_id", "starter_commit_sha", "starter_tree_sha"),
    )
    check(intent, "ck_start_actor", "actor_id = participant_id")
    check(
        intent,
        "ck_start_target",
        "btrim(provider_instance_key) <> '' AND working_branch = 'work' AND "
        "repository_slug = 'prodguild-project-' || replace(reserved_project_id::text, '-', '')",
    )
    result = _result(metadata, "project_start_results", intent.name)
    result.append_column(Column("project_id", Uuid, nullable=False))
    _external_id(result, "external_repository_id")
    _external_id(result, "actual_service_owner_id")
    sha(result, "workspace_base_commit_sha")
    sha(result, "workspace_base_tree_sha")
    result.append_column(Column("base_ref", String(63), nullable=False))
    bind(
        result,
        "fk_start_result_identity",
        ("operation_id", "project_id", "actual_service_owner_id"),
        intent.name,
        ("operation_id", "reserved_project_id", "service_owner_id"),
    )
    check(result, "ck_start_base_ref", "base_ref = 'refs/heads/prodguild/bases/' || project_id::text")
    result.append_constraint(
        UniqueConstraint(
            "operation_id", "workspace_base_commit_sha", "workspace_base_tree_sha", name="uq_start_observed_base"
        )
    )
    projects = metadata.tables["projects"]
    bind(
        projects,
        "fk_project_start",
        (
            "start_operation_id",
            "id",
            "repository_id",
            "participant_id",
            "case_id",
            "content_version_id",
            "case_version",
        ),
        intent.name,
        (
            "operation_id",
            "reserved_project_id",
            "reserved_repository_id",
            "participant_id",
            "case_id",
            "content_version_id",
            "content_version",
        ),
    )
    bind(
        projects,
        "fk_project_observed_base",
        ("start_operation_id", "workspace_base_commit_sha", "workspace_base_tree_sha"),
        result.name,
        ("operation_id", "workspace_base_commit_sha", "workspace_base_tree_sha"),
    )
    guards = Table(
        "project_mutation_guards",
        metadata,
        Column("project_id", Uuid, ForeignKey("projects.id"), primary_key=True),
        Column("active_operation_id", Uuid, ForeignKey("operations.id"), unique=True),
    )
    return intent, result, guards


def register_project_file_tables(metadata: MetaData) -> tuple[Table, Table]:
    intent = _intent(metadata, "project_file_intents")
    _project_target(intent)
    intent.append_column(Column("working_branch", String(4), nullable=False))
    intent.append_column(Column("verb", String(7), nullable=False))
    intent.append_column(Column("path", String, nullable=False))
    sha(intent, "expected_head_sha")
    sha(intent, "expected_blob_sha", nullable=True)
    intent.append_column(Column("message_present", Boolean, nullable=False))
    intent.append_column(Column("commit_message", String))
    intent.append_column(Column("content", LargeBinary))
    intent.append_column(Column("byte_length", Integer))
    intent.append_column(Column("content_sha256", String(64)))
    intent.append_constraint(UniqueConstraint("operation_id", "verb", name="uq_file_intent_verb"))
    check(intent, "ck_file_target", "working_branch = 'work' AND octet_length(path) BETWEEN 1 AND 1024")
    check(intent, "ck_file_verb", "verb IN ('create', 'replace', 'delete')")
    check(intent, "ck_file_expected_blob", "(verb = 'create') = (expected_blob_sha IS NULL)")
    check(intent, "ck_file_message_presence", "message_present = (commit_message IS NOT NULL)")
    check(
        intent,
        "ck_file_content",
        "(verb = 'delete' AND content IS NULL AND byte_length IS NULL AND content_sha256 IS NULL) OR "
        "(verb IN ('create', 'replace') AND content IS NOT NULL AND byte_length IS NOT NULL "
        "AND content_sha256 IS NOT NULL "
        "AND byte_length BETWEEN 0 AND 1048576 AND octet_length(content) = byte_length "
        "AND content_sha256 = encode(sha256(content), 'hex') AND content_sha256 ~ '^[0-9a-f]{64}$')",
    )
    # The adapter validates UTF-8 before insert and recovery repeats strict decode.
    # BYTEA preserves every byte, including U+0000; PostgreSQL TEXT cannot do that.
    result = _result(metadata, "project_file_results", intent.name)
    result.append_column(Column("verb", String(7), nullable=False))
    sha(result, "commit_sha")
    sha(result, "blob_sha", nullable=True)
    result.append_column(Column("byte_length", Integer))
    bind(result, "fk_file_result_verb", ("operation_id", "verb"), intent.name, ("operation_id", "verb"))
    check(
        result,
        "ck_file_result_content",
        "(verb = 'delete' AND blob_sha IS NULL AND byte_length IS NULL) OR "
        "(verb IN ('create', 'replace') AND blob_sha IS NOT NULL AND byte_length IS NOT NULL "
        "AND byte_length BETWEEN 0 AND 1048576)",
    )
    intent.append_constraint(UniqueConstraint("operation_id", "byte_length", name="uq_file_intent_length"))
    bind(result, "fk_file_result_length", ("operation_id", "byte_length"), intent.name, ("operation_id", "byte_length"))
    return intent, result


def register_submission_tables(metadata: MetaData) -> tuple[Table, Table, Table]:
    intent = _intent(metadata, "submission_intents")
    _project_target(intent)
    intent.append_column(Column("reserved_submission_id", Uuid, nullable=False, unique=True))
    intent.append_constraint(UniqueConstraint("project_id", name="uq_submission_intent_project"))
    intent.append_column(Column("content_version_id", Uuid, nullable=False))
    intent.append_column(Column("content_version", Integer, nullable=False))
    intent.append_column(Column("base_retained_reference_id", Uuid, nullable=False))
    sha(intent, "expected_commit_sha")
    sha(intent, "base_commit_sha")
    sha(intent, "base_tree_sha")
    intent.append_column(Column("summary_present", Boolean, nullable=False))
    intent.append_column(Column("summary", String(10000)))
    check(intent, "ck_submission_summary_presence", "summary_present = (summary IS NOT NULL)")
    bind(
        intent,
        "fk_submission_intent_content",
        ("project_id", "content_version_id", "content_version"),
        "projects",
        ("id", "content_version_id", "case_version"),
    )
    bind(
        intent,
        "fk_submission_intent_base",
        ("project_id", "base_retained_reference_id", "base_commit_sha", "base_tree_sha"),
        "projects",
        ("id", "base_retained_reference_id", "workspace_base_commit_sha", "workspace_base_tree_sha"),
    )
    intent.append_constraint(
        UniqueConstraint(
            "operation_id",
            "reserved_submission_id",
            "project_id",
            "repository_id",
            "actor_id",
            "content_version_id",
            "content_version",
            "base_retained_reference_id",
            name="uq_submission_intent_binding",
        )
    )
    intent.append_constraint(
        UniqueConstraint(
            "operation_id",
            "reserved_submission_id",
            "repository_id",
            "expected_commit_sha",
            "base_retained_reference_id",
            name="uq_submission_result_binding",
        )
    )
    result = _result(metadata, "submission_results", intent.name)
    for name in ("submission_id", "repository_id", "base_retained_reference_id", "retained_reference_id"):
        result.append_column(Column(name, Uuid, nullable=False))
    sha(result, "commit_sha")
    sha(result, "head_tree_sha")
    result.append_column(Column("head_ref", String(69), nullable=False))
    result.append_column(Column("provider_instance_key", String(100), nullable=False))
    for name in ("external_repository_id", "external_pr_id", "pr_number"):
        _external_id(result, name)
    check(result, "ck_submission_head_ref", "head_ref = 'refs/heads/prodguild/submissions/' || submission_id::text")
    check(result, "ck_submission_provider", "btrim(provider_instance_key) <> ''")
    bind(
        result,
        "fk_submission_result_intent",
        ("operation_id", "submission_id", "repository_id", "commit_sha", "base_retained_reference_id"),
        intent.name,
        (
            "operation_id",
            "reserved_submission_id",
            "repository_id",
            "expected_commit_sha",
            "base_retained_reference_id",
        ),
    )
    bind(
        result,
        "fk_submission_result_commit",
        ("retained_reference_id", "repository_id", "commit_sha"),
        "retained_references",
        ("id", "repository_id", "commit_id"),
    )
    result.append_constraint(
        UniqueConstraint(
            "operation_id", "submission_id", "repository_id", "retained_reference_id", name="uq_submission_result_head"
        )
    )
    result.append_constraint(
        UniqueConstraint("provider_instance_key", "external_repository_id", "pr_number", name="uq_submission_pr_number")
    )
    result.append_constraint(
        UniqueConstraint(
            "provider_instance_key", "external_repository_id", "external_pr_id", name="uq_submission_pr_id"
        )
    )
    submissions = metadata.tables["submissions"]
    bind(
        submissions,
        "fk_submission_intent",
        (
            "operation_id",
            "id",
            "project_id",
            "repository_id",
            "author_id",
            "content_version_id",
            "case_version",
            "base_retained_reference_id",
        ),
        intent.name,
        (
            "operation_id",
            "reserved_submission_id",
            "project_id",
            "repository_id",
            "actor_id",
            "content_version_id",
            "content_version",
            "base_retained_reference_id",
        ),
    )
    bind(
        submissions,
        "fk_submission_observed_head",
        ("operation_id", "id", "repository_id", "retained_reference_id"),
        result.name,
        ("operation_id", "submission_id", "repository_id", "retained_reference_id"),
    )
    # Immutable association: all provider/ref/object identities live in the typed result,
    # reachable by operation_id without network I/O or encoding a journal external_id.
    bindings = Table(
        "submission_git_bindings",
        metadata,
        Column("submission_id", Uuid, primary_key=True),
        Column("repository_id", Uuid, nullable=False),
        Column("operation_id", Uuid, ForeignKey("submission_results.operation_id"), nullable=False, unique=True),
    )
    bind(
        bindings,
        "fk_submission_git_binding",
        ("submission_id", "repository_id", "operation_id"),
        "submissions",
        ("id", "repository_id", "operation_id"),
    )
    return intent, result, bindings
