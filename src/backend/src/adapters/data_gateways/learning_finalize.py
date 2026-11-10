from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.adapters.data_gateways.learning_views import case_body, encode, row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.catalog.entity import CaseContentVersionId, CaseStatus, RepositoryId, RepositoryPurpose
from src.application.operations.idempotency import CachedResponse
from src.application.project.entity import ProjectState
from src.application.project.learning import GitPlan, LearningAction
from src.application.repository_storage.git import GitFile, GitSnapshot

if TYPE_CHECKING:
    from src.adapters.data_gateways.learning import DefaultLearningStore


async def finalize(
    store: DefaultLearningStore, plan: GitPlan, snapshot: GitSnapshot, files: tuple[GitFile, ...]
) -> CachedResponse:
    session, command = store.session, plan.command
    now = await database_now(session)
    action = command.action
    purpose = (
        RepositoryPurpose.WORKSPACE
        if action.value.startswith("project.")
        else RepositoryPurpose.PUBLISHED_MATERIALS
        if action == LearningAction.PUBLISH
        else RepositoryPurpose.REFERENCE
        if action.value.startswith("reference.")
        else RepositoryPurpose.MATERIALS
    )
    repositories, workspaces, retained = (
        meta_data.tables[name] for name in ("repositories", "git_workspaces", "retained_references")
    )
    await session.execute(
        pg_insert(repositories)
        .values(
            id=plan.repository_id,
            created_at=now,
            owner_id=plan.actor_id,
            provider="gitea",
            external_id=str(snapshot.repository_id),
            purpose=purpose,
        )
        .on_conflict_do_nothing(index_elements=[repositories.c.id])
    )
    await session.execute(
        pg_insert(workspaces)
        .values(
            repository_id=plan.repository_id,
            name=plan.repository_name,
            service_owner_id=snapshot.owner_id,
            provider_instance_key=store.instance_key,
            head_sha=snapshot.commit_sha,
            tree_sha=snapshot.tree_sha,
        )
        .on_conflict_do_update(
            index_elements=[workspaces.c.repository_id],
            set_={"head_sha": snapshot.commit_sha, "tree_sha": snapshot.tree_sha},
        )
    )
    reference_id = uuid4()
    ref = (
        f"refs/heads/prodguild/bases/{plan.project_id}"
        if action == LearningAction.START
        else f"refs/heads/prodguild/operations/{plan.operation_id}"
    )
    await session.execute(
        insert(retained).values(
            id=reference_id,
            created_at=now,
            repository_id=plan.repository_id,
            external_id=ref,
            commit_id=snapshot.commit_sha,
        )
    )
    if action == LearningAction.START:
        return await start_project(store, plan, snapshot, reference_id)
    if action.value.startswith("project."):
        content = next((file for file in files if file.path == command.path), None)
        await session.execute(
            insert(meta_data.tables["project_file_results"]).values(
                operation_id=plan.operation_id,
                observed_at=now,
                verb=action.value.split(".")[1],
                commit_sha=snapshot.commit_sha,
                blob_sha=content.sha if content else None,
                byte_length=content.size if content else None,
            )
        )
        projects = meta_data.tables["projects"]
        await session.execute(update(projects).where(projects.c.id == command.target_id).values(updated_at=now))
        return file_response(command.path, snapshot, content, creating=action == LearningAction.FILE_CREATE)
    case = await store.case_record(command.target_id, lock=True)
    references = meta_data.tables["reference_solutions"]
    if action == LearningAction.PUBLISH:
        item = await store.gateway.get_owned(case.owner_id, case.id)
        if item is None:
            raise ValueError("Publication case disappeared")
        item.metadata.require_publishable()
        if not plan.reuse_snapshot:
            await publish(store, plan, snapshot, reference_id)
        case.status = CaseStatus.PUBLISHED
    elif action == LearningAction.REFERENCE_INIT:
        await session.execute(
            insert(references).values(
                id=uuid4(),
                created_at=now,
                updated_at=now,
                case_id=case.id,
                repository_id=plan.repository_id,
                repository_purpose=RepositoryPurpose.REFERENCE,
                version=1,
            )
        )
    elif action.value.startswith("reference."):
        await session.execute(
            update(references)
            .where(references.c.case_id == case.id)
            .values(
                version=references.c.version + 1,
                updated_at=now,
                ready_reference_id=None,
                ready_at=None,
            )
        )
        case.current_published_content_id = None
    else:
        case.materials_repository_id = RepositoryId(plan.repository_id)
        case.current_published_content_id = None
    case.version += 1
    case.updated_at = now
    await session.flush()
    if action == LearningAction.PUBLISH:
        skills = meta_data.tables["case_competencies"]
        rows = (
            (await session.execute(select(skills).where(skills.c.case_id == case.id).order_by(skills.c.competency_id)))
            .mappings()
            .all()
        )
        return CachedResponse(status_code=200, body=case_body(case, [row_mapping(row) for row in rows]))
    if action == LearningAction.REFERENCE_INIT:
        return CachedResponse(
            status_code=201,
            body=encode(await store.views.reference(case.id)),
            location=f"/api/v1/admin/cases/{case.id}/reference-solution",
        )
    content = next((file for file in files if file.path == command.path), None)
    return file_response(
        command.path,
        snapshot,
        content,
        creating=action in {LearningAction.MATERIAL_CREATE, LearningAction.REFERENCE_CREATE},
    )


def file_response(path: str | None, snapshot: GitSnapshot, file: GitFile | None, *, creating: bool) -> CachedResponse:
    if file is None:
        return CachedResponse(status_code=204, body=b"")
    return CachedResponse(
        status_code=201 if creating else 200,
        body=encode({
            "path": path,
            "name": file.path.rsplit("/", 1)[-1],
            "type": "file",
            "sha": file.sha,
            "size": file.size,
            "commit_sha": snapshot.commit_sha,
            "content": None,
        }),
    )


async def publish(store: DefaultLearningStore, plan: GitPlan, snapshot: GitSnapshot, retained_id: UUID) -> None:
    session = store.session
    case = await store.case_record(plan.command.target_id, lock=True)
    references, retained, contents, skills = (
        meta_data.tables[name]
        for name in ("reference_solutions", "retained_references", "case_content_versions", "case_competencies")
    )
    reference = (await session.execute(select(references).where(references.c.case_id == case.id))).mappings().one()
    source_reference = await session.scalar(
        select(retained.c.id)
        .where(retained.c.repository_id == case.materials_repository_id, retained.c.commit_id == plan.source_commit)
        .order_by(retained.c.created_at, retained.c.id)
        .limit(1)
    )
    content_id, now = uuid4(), await database_now(session)
    await session.execute(
        insert(contents).values(
            id=content_id,
            created_at=now,
            case_id=case.id,
            content_version=case.version + 1,
            title=case.title,
            description=case.description,
            expected_result=case.expected_result,
            published_repository_id=plan.repository_id,
            published_retained_reference_id=retained_id,
            published_repository_purpose=RepositoryPurpose.PUBLISHED_MATERIALS,
            source_repository_id=case.materials_repository_id,
            source_retained_reference_id=source_reference,
            source_repository_purpose=RepositoryPurpose.MATERIALS,
            reference_solution_id=reference["id"],
            reference_version=reference["version"],
            reference_repository_id=reference["repository_id"],
            ready_reference_id=reference["ready_reference_id"],
            starter_commit_sha=snapshot.commit_sha,
            starter_tree_sha=snapshot.tree_sha,
        )
    )
    for skill in (
        await session.execute(select(skills).where(skills.c.case_id == case.id, skills.c.deleted_at.is_(None)))
    ).mappings():
        await session.execute(
            insert(meta_data.tables["case_content_competencies"]).values(
                id=uuid4(),
                created_at=now,
                content_version_id=content_id,
                competency_id=skill["competency_id"],
                required_value=skill["required_value"],
                weight=skill["weight"],
            )
        )
    case.current_published_content_id = CaseContentVersionId(content_id)


async def start_project(
    store: DefaultLearningStore, plan: GitPlan, snapshot: GitSnapshot, retained_id: UUID
) -> CachedResponse:
    if plan.project_id is None:
        raise ValueError("Project start plan has no reserved project")
    session = store.session
    now = await database_now(session)
    intents = meta_data.tables["project_start_intents"]
    intent = (
        (await session.execute(select(intents).where(intents.c.operation_id == plan.operation_id))).mappings().one()
    )
    await session.execute(
        insert(meta_data.tables["project_start_results"]).values(
            operation_id=plan.operation_id,
            observed_at=now,
            project_id=plan.project_id,
            external_repository_id=snapshot.repository_id,
            actual_service_owner_id=snapshot.owner_id,
            workspace_base_commit_sha=snapshot.commit_sha,
            workspace_base_tree_sha=snapshot.tree_sha,
            base_ref=f"refs/heads/prodguild/bases/{plan.project_id}",
        )
    )
    await session.execute(
        insert(meta_data.tables["projects"]).values(
            id=plan.project_id,
            created_at=now,
            updated_at=now,
            participant_id=plan.actor_id,
            case_id=intent["case_id"],
            case_version=intent["content_version"],
            repository_id=plan.repository_id,
            repository_purpose=RepositoryPurpose.WORKSPACE,
            working_branch="work",
            state=ProjectState.ACTIVE,
            content_version_id=intent["content_version_id"],
            start_operation_id=plan.operation_id,
            base_retained_reference_id=retained_id,
            workspace_base_commit_sha=snapshot.commit_sha,
            workspace_base_tree_sha=snapshot.tree_sha,
        )
    )
    await session.execute(insert(meta_data.tables["project_mutation_guards"]).values(project_id=plan.project_id))
    case = await store.case_record(value(intent, "case_id", UUID), lock=True)
    if case.frozen_at is None:
        case.frozen_at = now
    await session.flush()
    return CachedResponse(
        status_code=201,
        body=encode(await store.views.project(plan.actor_id, plan.project_id)),
        location=f"/api/v1/projects/{plan.project_id}",
    )
