"""Project-bound submission intentions and atomic immutable finalization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.review_jobs import enqueue_submission
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.catalog.management import CaseConflictError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.operations.entity import Operation
from src.application.operations.idempotency import CachedResponse
from src.application.project.entity import ProjectState
from src.application.project.learning import GitPlan, LearningCommand
from src.application.repository_storage.git import GitPullRequest, GitSnapshot
from src.application.repository_storage.ports import StorageError
from src.application.user.entity import UserId

if TYPE_CHECKING:
    from src.adapters.data_gateways.learning import DefaultLearningStore


async def plan_submission(
    store: DefaultLearningStore, operation: Operation, actor: UserId, command: LearningCommand, owner: int
) -> GitPlan:
    projects = meta_data.tables["projects"]
    project = (
        (await store.session.execute(select(projects).where(projects.c.id == command.target_id).with_for_update()))
        .mappings()
        .one()
    )
    if project["state"] != ProjectState.ACTIVE:
        raise CaseConflictError("PROJECT_SUBMITTED", "Project already submitted")
    requirements, competencies = (meta_data.tables[name] for name in ("case_content_competencies", "user_competencies"))
    skill_ids = list(
        await store.session.scalars(
            select(requirements.c.competency_id).where(
                requirements.c.content_version_id == project["content_version_id"],
            )
        )
    )
    if not skill_ids:
        raise CaseConflictError("CASE_NOT_READY", "Published requirements are missing")
    unavailable = await store.session.scalar(
        select(competencies.c.id)
        .where(
            competencies.c.user_id == actor,
            competencies.c.competency_id.in_(skill_ids),
            competencies.c.deleted_at.is_not(None),
        )
        .limit(1)
    )
    if unavailable is not None:
        raise CaseConflictError("SKILL_UNAVAILABLE", "Author skill record is unavailable")
    repository_id = value(project, "repository_id", UUID)
    workspace = await store.workspace(repository_id)
    if workspace["service_owner_id"] != owner:
        raise CaseConflictError("STORAGE_CHANGED", "Repository storage owner changed")
    if workspace["head_sha"] != command.expected_commit_sha:
        raise CaseConflictError("COMMIT_CHANGED", "Project HEAD changed")
    if workspace["tree_sha"] == project["workspace_base_tree_sha"]:
        raise CaseConflictError("NO_CHANGES", "Change the starter files before submitting")
    repositories = meta_data.tables["repositories"]
    external_id = await store.session.scalar(
        select(repositories.c.external_id).where(repositories.c.id == repository_id)
    )
    if not isinstance(external_id, str) or not external_id.isdecimal() or int(external_id) < 1:
        raise StorageError
    return GitPlan(
        operation_id=operation.id,
        actor_id=actor,
        command=command,
        repository_id=repository_id,
        repository_name=value(workspace, "name", str),
        owner_id=owner,
        head_sha=value(workspace, "head_sha", str),
        branch="work",
        project_id=command.target_id,
        submission_id=uuid4(),
        base_commit_sha=value(project, "workspace_base_commit_sha", str),
        base_tree_sha=value(project, "workspace_base_tree_sha", str),
        external_repository_id=int(external_id),
    )


async def submission_intent(store: DefaultLearningStore, plan: GitPlan, digest: str, now: datetime) -> None:
    projects = meta_data.tables["projects"]
    project = (await store.session.execute(select(projects).where(projects.c.id == plan.project_id))).mappings().one()
    await store.session.execute(
        insert(meta_data.tables["submission_intents"]).values(
            operation_id=plan.operation_id,
            created_at=now,
            payload_namespace="prodguild.learning.v1",
            payload_hash=digest,
            actor_id=plan.actor_id,
            project_id=plan.project_id,
            repository_id=plan.repository_id,
            reserved_submission_id=plan.submission_id,
            content_version_id=project["content_version_id"],
            content_version=project["case_version"],
            base_retained_reference_id=project["base_retained_reference_id"],
            expected_commit_sha=plan.head_sha,
            base_commit_sha=plan.base_commit_sha,
            base_tree_sha=plan.base_tree_sha,
            summary_present=plan.command.summary is not None,
            summary=plan.command.summary,
        )
    )


async def finalize_submission(
    store: DefaultLearningStore, plan: GitPlan, snapshot: GitSnapshot, pull: GitPullRequest | None
) -> CachedResponse:
    if (
        pull is None
        or plan.submission_id is None
        or pull.repository_id != snapshot.repository_id
        or pull.repository_id != plan.external_repository_id
        or pull.owner_id != plan.owner_id
        or pull.base_sha != plan.base_commit_sha
        or pull.head_sha != plan.head_sha
        or snapshot.commit_sha != plan.head_sha
    ):
        raise StorageError
    session, now = store.session, await database_now(store.session)
    intents = meta_data.tables["submission_intents"]
    intent = (
        (await session.execute(select(intents).where(intents.c.operation_id == plan.operation_id))).mappings().one()
    )
    retained_id = uuid4()
    head_ref = f"refs/heads/prodguild/submissions/{plan.submission_id}"
    await session.execute(
        insert(meta_data.tables["retained_references"]).values(
            id=retained_id,
            created_at=now,
            repository_id=plan.repository_id,
            external_id=head_ref,
            commit_id=snapshot.commit_sha,
        )
    )
    await session.execute(
        insert(meta_data.tables["submission_results"]).values(
            operation_id=plan.operation_id,
            observed_at=now,
            submission_id=plan.submission_id,
            repository_id=plan.repository_id,
            base_retained_reference_id=intent["base_retained_reference_id"],
            retained_reference_id=retained_id,
            commit_sha=snapshot.commit_sha,
            head_tree_sha=snapshot.tree_sha,
            head_ref=head_ref,
            provider_instance_key=store.instance_key,
            external_repository_id=pull.repository_id,
            external_pr_id=pull.external_id,
            pr_number=pull.number,
        )
    )
    await session.execute(
        insert(meta_data.tables["submissions"]).values(
            id=plan.submission_id,
            created_at=now,
            project_id=plan.project_id,
            repository_id=plan.repository_id,
            retained_reference_id=retained_id,
            case_version=intent["content_version"],
            operation_id=plan.operation_id,
            content_version_id=intent["content_version_id"],
            author_id=plan.actor_id,
            base_retained_reference_id=intent["base_retained_reference_id"],
        )
    )
    await snapshot_author(store, plan, value(intent, "content_version_id", UUID), now)
    await enqueue_submission(session, plan.submission_id)
    await session.execute(
        insert(meta_data.tables["submission_git_bindings"]).values(
            submission_id=plan.submission_id, repository_id=plan.repository_id, operation_id=plan.operation_id
        )
    )
    projects = meta_data.tables["projects"]
    await session.execute(
        update(projects).where(projects.c.id == plan.project_id).values(state=ProjectState.SUBMITTED, updated_at=now)
    )
    return CachedResponse(
        status_code=201,
        body=await store.views.submission(plan.actor_id, plan.submission_id),
        location=f"/api/v1/submissions/{plan.submission_id}",
    )


async def snapshot_author(store: DefaultLearningStore, plan: GitPlan, content_id: UUID, now: datetime) -> None:
    session = store.session
    requirements, competencies, grants = (
        meta_data.tables[name] for name in ("case_content_competencies", "user_competencies", "skill_grants")
    )
    skills = (
        (
            await session.execute(
                select(requirements)
                .where(requirements.c.content_version_id == content_id)
                .order_by(requirements.c.competency_id)
            )
        )
        .mappings()
        .all()
    )
    if not skills:
        raise CaseConflictError("CASE_NOT_READY", "Published requirements are missing")
    for requirement in skills:
        skill_id = value(requirement, "competency_id", UUID)
        await session.execute(
            pg_insert(competencies)
            .values(
                id=uuid4(),
                created_at=now,
                user_id=plan.actor_id,
                competency_id=skill_id,
                contribution_sum=0,
                displayed_value=0,
                grade=SkillGrade.PERFORMER,
                version=1,
            )
            .on_conflict_do_nothing(index_elements=[competencies.c.user_id, competencies.c.competency_id])
        )
        competency = (
            (
                await session.execute(
                    select(competencies)
                    .where(competencies.c.user_id == plan.actor_id, competencies.c.competency_id == skill_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if competency["deleted_at"] is not None:
            raise CaseConflictError("SKILL_UNAVAILABLE", "Author skill record is unavailable")
        history = (
            (
                await session.execute(
                    select(grants)
                    .where(
                        grants.c.user_id == plan.actor_id,
                        grants.c.competency_id == skill_id,
                        grants.c.deleted_at.is_(None),
                    )
                    .order_by(grants.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        live = [grant for grant in history if grant["state"] != SkillGrantState.REVOKED]
        # Preserve latest revocation evidence when there is no current grant. It never grants rights.
        grant = (
            live[0]
            if live
            else max(
                history, key=lambda item: (value(item, "revoked_at", datetime), value(item, "id", UUID)), default=None
            )
        )
        facts: dict[str, object] = {
            "id": uuid4(),
            "created_at": now,
            "submission_id": plan.submission_id,
            "competency_id": skill_id,
            "required_value": requirement["required_value"],
            "weight": requirement["weight"],
            "content_version_id": content_id,
            "author_id": plan.actor_id,
            "author_value": competency["displayed_value"],
            "author_grade": competency["grade"],
            "author_user_competency_version": competency["version"],
            "author_snapshotted_at": now,
        }
        facts.update(grant_facts(row_mapping(grant) if grant is not None else None))
        await session.execute(insert(meta_data.tables["submission_competencies"]).values(**facts))


def grant_facts(grant: Mapping[str, object] | None) -> dict[str, object]:
    if grant is None:
        return {}
    mapping = {
        "id": "grant_id",
        "version": "grant_version",
        "state": "grant_state",
        "grade": "grant_grade",
        "user_competency_version": "grant_user_competency_version",
        "evidence_kind": "grant_evidence_kind",
        "evidence": "grant_evidence",
        "readiness_assessment_id": "readiness_assessment_id",
        "confirmed_by_user_id": "confirmed_by_user_id",
        "confirmed_by_grant_id": "confirmed_by_grant_id",
        "confirmed_by_grant_version": "confirmed_by_grant_version",
        "seed_reference": "seed_reference",
        "confirmed_at": "grant_confirmed_at",
        "revoked_at": "grant_revoked_at",
    }
    return {"author_" + target: grant[source] for source, target in mapping.items()}
