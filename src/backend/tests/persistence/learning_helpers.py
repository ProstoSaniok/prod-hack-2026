"""Synthetic records for migrated-schema checks, never Git/ACL acceptance evidence."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Connection

from src.adapters.data_gateways.tables import meta_data
from tests.persistence.helpers import insert


@dataclass(frozen=True)
class Publication:
    author: UUID
    case: UUID
    content: UUID
    skill: UUID
    published_repository: UUID
    published_reference: UUID
    reference_solution: UUID


@dataclass(frozen=True)
class Workspace:
    publication: Publication
    project: UUID
    repository: UUID
    base_reference: UUID


def operation(connection: Connection, kind: str, subject: UUID) -> UUID:
    now = datetime.now(UTC)
    return insert(
        connection,
        "operations",
        kind=kind,
        subject_id=subject,
        source_key=str(uuid4()),
        payload_hash="a" * 64,
        available_at=now,
        state="PENDING",
        attempts=0,
    )


def publication(connection: Connection) -> Publication:
    now = datetime.now(UTC)
    owner, author = (insert(connection, "users", role="USER") for _ in range(2))
    private, published, reference = (
        insert(
            connection, "repositories", owner_id=owner, provider="synthetic", external_id=str(uuid4()), purpose=purpose
        )
        for purpose in ("MATERIALS", "PUBLISHED_MATERIALS", "REFERENCE")
    )
    private_ref, published_ref, ready_ref = (
        insert(
            connection, "retained_references", repository_id=repo, external_id=f"refs/heads/{label}", commit_id=sha * 40
        )
        for repo, label, sha in (
            (private, "private", "1"),
            (published, "published", "2"),
            (reference, "private-reference", "3"),
        )
    )
    case = insert(
        connection,
        "cases",
        owner_id=owner,
        title="Case",
        description="Private authoring",
        expected_result="Result",
        status="DRAFT",
        materials_repository_id=private,
        updated_at=now,
    )
    reference_solution = insert(
        connection,
        "reference_solutions",
        case_id=case,
        repository_id=reference,
        ready_reference_id=ready_ref,
        ready_at=now,
        updated_at=now,
    )
    content = insert(
        connection,
        "case_content_versions",
        case_id=case,
        content_version=1,
        title="Public snapshot",
        description="Clean retained content",
        expected_result="Result",
        published_repository_id=published,
        published_retained_reference_id=published_ref,
        published_repository_purpose="PUBLISHED_MATERIALS",
        starter_commit_sha="2" * 40,
        starter_tree_sha="4" * 40,
        source_repository_id=private,
        source_retained_reference_id=private_ref,
        source_repository_purpose="MATERIALS",
        reference_solution_id=reference_solution,
        reference_version=1,
        reference_repository_id=reference,
        ready_reference_id=ready_ref,
    )
    skill = insert(connection, "competencies", name=str(uuid4()), description="SQL")
    insert(
        connection,
        "case_content_competencies",
        content_version_id=content,
        competency_id=skill,
        required_value=Decimal("20.12345678901234567890123456789"),
        weight=Decimal("0.125"),
    )
    cases = meta_data.tables["cases"]
    connection.execute(
        cases.update().where(cases.c.id == case).values(current_published_content_id=content, status="PUBLISHED")
    )
    return Publication(author, case, content, skill, published, published_ref, reference_solution)


def workspace(connection: Connection) -> Workspace:
    published = publication(connection)
    project, repository = uuid4(), uuid4()
    op = operation(connection, "project.start", project)
    connection.execute(
        meta_data.tables["project_start_intents"]
        .insert()
        .values(
            operation_id=op,
            created_at=datetime.now(UTC),
            payload_namespace="prodguild-json-v1",
            payload_hash="a" * 64,
            reserved_project_id=project,
            reserved_repository_id=repository,
            actor_id=published.author,
            participant_id=published.author,
            case_id=published.case,
            content_version_id=published.content,
            content_version=1,
            starter_repository_id=published.published_repository,
            starter_retained_reference_id=published.published_reference,
            starter_commit_sha="2" * 40,
            source_tree_sha="4" * 40,
            provider_instance_key="synthetic",
            service_owner_id=1,
            repository_slug=f"prodguild-project-{project.hex}",
            working_branch="work",
        )
    )
    connection.execute(
        meta_data.tables["project_start_results"]
        .insert()
        .values(
            operation_id=op,
            project_id=project,
            external_repository_id=123,
            actual_service_owner_id=1,
            workspace_base_commit_sha="5" * 40,
            workspace_base_tree_sha="4" * 40,
            base_ref=f"refs/heads/prodguild/bases/{project}",
            observed_at=datetime.now(UTC),
        )
    )
    insert(
        connection,
        "repositories",
        id=repository,
        owner_id=published.author,
        provider="synthetic",
        external_id="123",
        purpose="WORKSPACE",
    )
    base = insert(
        connection,
        "retained_references",
        repository_id=repository,
        external_id=f"refs/heads/prodguild/bases/{project}",
        commit_id="5" * 40,
    )
    insert(
        connection,
        "projects",
        id=project,
        participant_id=published.author,
        case_id=published.case,
        case_version=1,
        repository_id=repository,
        working_branch="work",
        state="ACTIVE",
        content_version_id=published.content,
        start_operation_id=op,
        base_retained_reference_id=base,
        workspace_base_commit_sha="5" * 40,
        workspace_base_tree_sha="4" * 40,
        updated_at=datetime.now(UTC),
    )
    return Workspace(published, project, repository, base)


def submission(connection: Connection, work: Workspace) -> UUID:
    subject = uuid4()
    op = operation(connection, "project.submit", subject)
    connection.execute(
        meta_data.tables["submission_intents"]
        .insert()
        .values(
            operation_id=op,
            created_at=datetime.now(UTC),
            payload_namespace="prodguild-json-v1",
            payload_hash="a" * 64,
            reserved_submission_id=subject,
            project_id=work.project,
            actor_id=work.publication.author,
            repository_id=work.repository,
            content_version_id=work.publication.content,
            content_version=1,
            expected_commit_sha="6" * 40,
            base_retained_reference_id=work.base_reference,
            base_commit_sha="5" * 40,
            base_tree_sha="4" * 40,
            summary_present=True,
            summary="",
        )
    )
    head = insert(
        connection,
        "retained_references",
        repository_id=work.repository,
        external_id=f"refs/heads/prodguild/submissions/{subject}",
        commit_id="6" * 40,
    )
    connection.execute(
        meta_data.tables["submission_results"]
        .insert()
        .values(
            operation_id=op,
            submission_id=subject,
            repository_id=work.repository,
            base_retained_reference_id=work.base_reference,
            retained_reference_id=head,
            commit_sha="6" * 40,
            head_tree_sha="7" * 40,
            head_ref=f"refs/heads/prodguild/submissions/{subject}",
            provider_instance_key="synthetic",
            external_repository_id=123,
            external_pr_id=90,
            pr_number=1,
            observed_at=datetime.now(UTC),
        )
    )
    insert(
        connection,
        "submissions",
        id=subject,
        project_id=work.project,
        repository_id=work.repository,
        retained_reference_id=head,
        case_version=1,
        operation_id=op,
        content_version_id=work.publication.content,
        author_id=work.publication.author,
        base_retained_reference_id=work.base_reference,
    )
    connection.execute(
        meta_data.tables["submission_git_bindings"]
        .insert()
        .values(submission_id=subject, repository_id=work.repository, operation_id=op)
    )
    return subject
