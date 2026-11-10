"""Immutable publication and canonical learning bindings; no use-case registration."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    inspect,
)
from sqlalchemy.orm import registry

from src.adapters.data_gateways.core_tables import enum_column, identity_columns
from src.adapters.data_gateways.immutable_evidence import protect_evidence
from src.application.catalog.entity import (
    Case,
    CaseContentCompetency,
    CaseContentVersion,
    ReferenceSolution,
    RepositoryPurpose,
)
from src.application.project.entity import Project, Submission


def check(table: Table, name: str, expression: str) -> None:
    table.append_constraint(CheckConstraint(expression, name=name, info={"prodguild_learning_check": True}))


def sha(table: Table, name: str, *, nullable: bool = False) -> None:
    table.append_column(Column(name, String(40), nullable=nullable))
    check(table, f"ck_{table.name}_{name}", f"{name} ~ '^[0-9a-f]{{40}}$'")


def bind(table: Table, name: str, local: tuple[str, ...], remote: str, target: tuple[str, ...]) -> None:
    table.append_constraint(
        ForeignKeyConstraint(list(local), [f"{remote}.{column}" for column in target], name=name, use_alter=True)
    )


def map_added_columns(entity: type[object], table: Table) -> None:
    mapper = inspect(entity)
    if mapper is None:
        raise TypeError("Learning extensions require an existing canonical mapper")
    for column in table.columns:
        if column.key not in mapper.attrs:
            mapper.add_property(column.key, column)


def register_publication_tables(metadata: MetaData, mappers: registry) -> tuple[Table, Table]:
    references, cases = metadata.tables["retained_references"], metadata.tables["cases"]
    references.append_constraint(UniqueConstraint("id", "repository_id", "commit_id", name="uq_retained_commit"))
    reference = metadata.tables["reference_solutions"]
    reference.append_constraint(UniqueConstraint("id", "case_id", "repository_id", name="uq_reference_case_repo"))
    reference.append_column(Column("updated_at", DateTime(timezone=True)))
    cases.append_column(Column("updated_at", DateTime(timezone=True)))
    cases.append_column(Column("current_published_content_id", Uuid))
    cases.append_constraint(UniqueConstraint("id", "materials_repository_id", name="uq_case_authoring_repo"))
    contents = Table(
        "case_content_versions",
        metadata,
        *identity_columns(),
        Column("case_id", Uuid, ForeignKey("cases.id"), nullable=False),
        Column("content_version", Integer, nullable=False),
        Column("title", String(500), nullable=False),
        Column("description", String, nullable=False),
        Column("expected_result", String, nullable=False),
        Column("published_repository_id", Uuid, nullable=False),
        Column("published_retained_reference_id", Uuid, nullable=False),
        enum_column("published_repository_purpose", RepositoryPurpose),
        Column("source_repository_id", Uuid, nullable=False),
        Column("source_retained_reference_id", Uuid, nullable=False),
        enum_column("source_repository_purpose", RepositoryPurpose),
        Column("reference_solution_id", Uuid, nullable=False),
        Column("reference_version", Integer, nullable=False),
        Column("reference_repository_id", Uuid, nullable=False),
        Column("ready_reference_id", Uuid, nullable=False),
        UniqueConstraint("case_id", "content_version", name="uq_content_case_version"),
        UniqueConstraint("id", "case_id", name="uq_content_case"),
        UniqueConstraint("id", "case_id", "content_version", name="uq_content_identity"),
    )
    sha(contents, "starter_commit_sha")
    sha(contents, "starter_tree_sha")
    bind(
        contents,
        "fk_content_authoring_case",
        ("case_id", "source_repository_id"),
        "cases",
        ("id", "materials_repository_id"),
    )
    contents.append_constraint(
        UniqueConstraint(
            "id",
            "published_repository_id",
            "published_retained_reference_id",
            "starter_commit_sha",
            "starter_tree_sha",
            name="uq_content_starter",
        )
    )
    for prefix, purpose in (("published", "PUBLISHED_MATERIALS"), ("source", "MATERIALS")):
        bind(
            contents,
            f"fk_content_{prefix}_purpose",
            (f"{prefix}_repository_id", f"{prefix}_repository_purpose"),
            "repositories",
            ("id", "purpose"),
        )
        bind(
            contents,
            f"fk_content_{prefix}_reference",
            (f"{prefix}_retained_reference_id", f"{prefix}_repository_id"),
            "retained_references",
            ("id", "repository_id"),
        )
        check(contents, f"ck_content_{prefix}_purpose", f"{prefix}_repository_purpose = '{purpose}'")
    bind(
        contents,
        "fk_content_published_commit",
        ("published_retained_reference_id", "published_repository_id", "starter_commit_sha"),
        "retained_references",
        ("id", "repository_id", "commit_id"),
    )
    bind(
        contents,
        "fk_content_reference_case",
        ("reference_solution_id", "case_id", "reference_repository_id"),
        "reference_solutions",
        ("id", "case_id", "repository_id"),
    )
    bind(
        contents,
        "fk_content_ready_reference",
        ("ready_reference_id", "reference_repository_id"),
        "retained_references",
        ("id", "repository_id"),
    )
    check(contents, "ck_content_versions", "content_version > 0 AND reference_version > 0")
    check(
        contents,
        "ck_content_distinct_repos",
        "published_repository_id <> source_repository_id AND "
        "published_repository_id <> reference_repository_id AND source_repository_id <> reference_repository_id",
    )
    check(
        contents,
        "ck_content_complete",
        "btrim(title) <> '' AND btrim(description) <> '' AND btrim(expected_result) <> ''",
    )
    bind(
        cases,
        "fk_case_current_content",
        ("current_published_content_id", "id"),
        "case_content_versions",
        ("id", "case_id"),
    )
    skills = Table(
        "case_content_competencies",
        metadata,
        *identity_columns(),
        Column("content_version_id", Uuid, ForeignKey("case_content_versions.id"), nullable=False),
        Column("competency_id", Uuid, ForeignKey("competencies.id"), nullable=False),
        Column("required_value", Numeric, nullable=False),
        Column("weight", Numeric, nullable=False),
        UniqueConstraint("content_version_id", "competency_id", name="uq_content_skill"),
        UniqueConstraint(
            "content_version_id", "competency_id", "required_value", "weight", name="uq_content_skill_values"
        ),
        CheckConstraint("required_value BETWEEN 0 AND 100", name="ck_content_required_value"),
        CheckConstraint("weight > 0 AND weight <= 0.25", name="ck_content_weight"),
    )
    for entity, table in ((CaseContentVersion, contents), (CaseContentCompetency, skills)):
        mappers.map_imperatively(entity, table)
        protect_evidence(entity)
    map_added_columns(Case, cases)
    map_added_columns(ReferenceSolution, reference)
    return contents, skills


def extend_projects(metadata: MetaData) -> None:
    projects = metadata.tables["projects"]
    for name in ("content_version_id", "start_operation_id", "base_retained_reference_id"):
        projects.append_column(Column(name, Uuid))
    projects.append_column(Column("updated_at", DateTime(timezone=True)))
    sha(projects, "workspace_base_commit_sha", nullable=True)
    sha(projects, "workspace_base_tree_sha", nullable=True)
    projects.append_constraint(UniqueConstraint("id", "repository_id", "participant_id", name="uq_project_actor_repo"))
    projects.append_constraint(UniqueConstraint("id", "content_version_id", "case_version", name="uq_project_content"))
    projects.append_constraint(
        UniqueConstraint(
            "id",
            "base_retained_reference_id",
            "workspace_base_commit_sha",
            "workspace_base_tree_sha",
            name="uq_project_base",
        )
    )
    bind(
        projects,
        "fk_project_content",
        ("content_version_id", "case_id", "case_version"),
        "case_content_versions",
        ("id", "case_id", "content_version"),
    )
    bind(
        projects,
        "fk_project_base_commit",
        ("base_retained_reference_id", "repository_id", "workspace_base_commit_sha"),
        "retained_references",
        ("id", "repository_id", "commit_id"),
    )
    check(
        projects,
        "ck_project_learning_binding",
        "(content_version_id IS NULL AND start_operation_id IS NULL "
        "AND base_retained_reference_id IS NULL AND workspace_base_commit_sha IS NULL "
        "AND workspace_base_tree_sha IS NULL) OR (content_version_id IS NOT NULL AND start_operation_id IS NOT NULL "
        "AND base_retained_reference_id IS NOT NULL "
        "AND workspace_base_commit_sha IS NOT NULL AND workspace_base_tree_sha IS NOT NULL AND updated_at IS NOT NULL "
        "AND working_branch = 'work')",
    )
    submissions = metadata.tables["submissions"]
    for name in ("operation_id", "content_version_id", "author_id", "base_retained_reference_id"):
        submissions.append_column(Column(name, Uuid))
    submissions.append_constraint(
        UniqueConstraint("id", "author_id", "content_version_id", name="uq_submission_author_content")
    )
    submissions.append_constraint(
        UniqueConstraint("id", "repository_id", "operation_id", name="uq_submission_git_binding")
    )
    bind(
        submissions,
        "fk_submission_author",
        ("project_id", "repository_id", "author_id"),
        "projects",
        ("id", "repository_id", "participant_id"),
    )
    bind(
        submissions,
        "fk_submission_content",
        ("project_id", "content_version_id", "case_version"),
        "projects",
        ("id", "content_version_id", "case_version"),
    )
    bind(
        submissions,
        "fk_submission_base_repository",
        ("base_retained_reference_id", "repository_id"),
        "retained_references",
        ("id", "repository_id"),
    )
    check(
        submissions,
        "ck_submission_learning_binding",
        "(operation_id IS NULL AND content_version_id IS NULL "
        "AND author_id IS NULL AND base_retained_reference_id IS NULL) OR (operation_id IS NOT NULL "
        "AND content_version_id IS NOT NULL AND author_id IS NOT NULL AND base_retained_reference_id IS NOT NULL)",
    )
    map_added_columns(Project, projects)
    map_added_columns(Submission, submissions)
