import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.case_management import DefaultCaseGateway
from src.adapters.data_gateways.learning_finalize import finalize
from src.adapters.data_gateways.learning_views import LearningViews, encode, row_mapping
from src.adapters.data_gateways.submission import finalize_submission, plan_submission, submission_intent
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.learning_requests import LearningRequests
from src.adapters.operations.sql_records import operation_from_row, optional, value, work_values
from src.adapters.operations.sql_work import SqlOperationJournal, database_now
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.entity import Case, CaseId, CaseStatus
from src.application.catalog.management import CaseConflictError, CaseNotFoundError, CaseValidationError
from src.application.operations.entity import Operation, OperationSpec, SourceConflictError
from src.application.operations.idempotency import CachedResponse
from src.application.operations.work import FailureCode, RetryPolicy, Work, WorkState
from src.application.project.entity import ProjectState
from src.application.project.learning import GitPlan, LearningAction, LearningCommand
from src.application.repository_storage.git import GitFile, GitPullRequest, GitSnapshot
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserId

PLAN: TypeAdapter[GitPlan] = TypeAdapter(GitPlan)
POLICY = RetryPolicy(lease_for=timedelta(seconds=120), io_timeout=timedelta(seconds=45))
START = LearningAction.START
PUBLISH = LearningAction.PUBLISH
INIT = LearningAction.REFERENCE_INIT
SUBMIT = LearningAction.SUBMIT


def command_payload(command: LearningCommand) -> bytes:
    payload = asdict(command)
    if command.action != SUBMIT:
        # Preserve the digests of requests/operations persisted before submissions shipped.
        del payload["expected_commit_sha"], payload["summary"]
    return encode(payload)


class DefaultLearningStore:
    def __init__(self, session: AsyncSession, instance_key: str) -> None:
        self.session, self.instance_key = session, instance_key
        self.gateway, self.views = DefaultCaseGateway(session), LearningViews(session)
        self.operations = SqlOperationJournal(session, meta_data.tables["operations"])
        self.claimed: Operation | None = None
        self.preflight_head: str | None = None
        self.preflight_source: tuple[str, str] | None = None
        self.requests = LearningRequests(session)

    async def authorize(self, actor: UserId, command: LearningCommand, *, lock: bool = False) -> None:
        if command.action == START:
            await self.participant(actor, lock=lock)
            existing = await self._existing_project(actor, command.target_id)
            if existing is None:
                await self.case_record(command.target_id, published=True)
        elif command.action.value.startswith("project."):
            await self.participant(actor, lock=lock)
            await self.views.project(actor, command.target_id)
        else:
            item = await self.gateway.get_owned(actor, CaseId(command.target_id))
            if item is None or item.case.deleted_at is not None:
                raise CaseNotFoundError
            await self.gateway.require_organizer(actor, lock=lock)

    async def participant(self, actor: UserId, *, lock: bool = False) -> None:
        grants, users = meta_data.tables["user_capabilities"], meta_data.tables["users"]
        statement = select(grants.c.id)
        statement = statement.join(users, users.c.id == grants.c.user_id).where(
            grants.c.user_id == actor,
            grants.c.capability == ProductCapability.PARTICIPANT,
            grants.c.deleted_at.is_(None),
            grants.c.revoked_at.is_(None),
            users.c.deleted_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update(read=True)
        found = await self.session.scalar(statement)
        if found is None:
            raise ForbiddenError

    @staticmethod
    def source_key(actor: UserId, command: LearningCommand, key: str) -> str:
        if command.action in {START, INIT, SUBMIT}:
            return f"{actor}:{command.target_id}"
        return hashlib.sha256(f"{actor}:{command.action}:{command.target_id}:{key}".encode()).hexdigest()

    async def preflight(self, actor: UserId, command: LearningCommand, key: str) -> tuple[str, str] | None:
        operations = meta_data.tables["operations"]
        existing = await self.session.scalar(
            select(operations.c.id).where(
                operations.c.kind == f"learning.{command.action}",
                operations.c.source_key == self.source_key(actor, command, key),
            )
        )
        if existing is not None:
            return None  # Recovery must inspect the persisted intention, not reject its own applied effect.
        if command.action in {PUBLISH, INIT, START}:
            case = await self.case_record(command.target_id, published=command.action == START)
            source = await self._source(case, command.action)
            if command.action == PUBLISH and case.current_published_content_id is not None:
                source = await self._source(case, START)
            self.preflight_source = value(source, "head_sha", str), value(source, "tree_sha", str)
            await self.session.commit()
            return value(source, "name", str), value(source, "head_sha", str)
        if command.path is None and command.action != SUBMIT:
            return None
        if command.action.value.startswith("project."):
            projects = meta_data.tables["projects"]
            repository = await self.session.scalar(
                select(projects.c.repository_id).where(projects.c.id == command.target_id)
            )
        elif command.action.value.startswith("reference."):
            references = meta_data.tables["reference_solutions"]
            repository = await self.session.scalar(
                select(references.c.repository_id).where(references.c.case_id == command.target_id)
            )
        else:
            repository = (await self.case_record(command.target_id)).materials_repository_id
        if repository is None:
            return None
        if not isinstance(repository, UUID):
            raise TypeError("Invalid learning repository identity")
        workspace = await self.workspace(repository)
        self.preflight_head = value(workspace, "head_sha", str)
        # Close the read transaction before Git I/O; prepare checks optimistic versions again.
        await self.session.commit()
        return value(workspace, "name", str), value(workspace, "head_sha", str)

    async def case_record(self, case_id: UUID, *, published: bool = False, lock: bool = False) -> Case:
        table = meta_data.tables["cases"]
        query = select(Case).where(table.c.id == case_id, table.c.deleted_at.is_(None))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        case = await self.session.scalar(query)
        if case is None or (
            published and (case.status != CaseStatus.PUBLISHED or case.current_published_content_id is None)
        ):
            raise CaseNotFoundError
        return case

    async def _existing_project(self, actor: UserId, case_id: UUID) -> UUID | None:
        table = meta_data.tables["projects"]
        return await self.session.scalar(
            select(table.c.id).where(table.c.participant_id == actor, table.c.case_id == case_id)
        )

    async def prepare(
        self, actor: UserId, command: LearningCommand, key: str, service_owner: int
    ) -> GitPlan | CachedResponse:
        if re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", key) is None:
            raise CaseValidationError("Invalid request key")
        if command.content is not None and len(command.content.encode()) > 1_048_576:
            raise CaseValidationError("File exceeds 1 MiB")
        source = self.source_key(actor, command, key)
        digest = hashlib.sha256(command_payload(command)).hexdigest()
        business_digest = (
            hashlib.sha256(command_payload(replace(command, expected_version=None))).hexdigest()
            if command.action == INIT
            else digest
        )
        now = await database_now(self.session)
        candidate = Operation(
            id=uuid4(),
            spec=OperationSpec(
                kind=f"learning.{command.action}",
                source_key=source,
                subject_id=command.target_id,
                payload_hash=business_digest,
            ),
            work=Work(created_at=now, available_at=now),
        )
        try:
            operation = await self.operations.reserve(candidate)
        except SourceConflictError:
            if command.action != SUBMIT:
                raise
            # reserve holds the permanent operation lock. Retained key conflicts
            # take precedence over the one-submission-per-project business conflict.
            operations = meta_data.tables["operations"]
            existing = (
                (
                    await self.session.execute(
                        select(operations).where(
                            operations.c.kind == candidate.spec.kind,
                            operations.c.source_key == source,
                        )
                    )
                )
                .mappings()
                .one()
            )
            await self.requests.reserve(value(existing, "id", UUID), actor, command, key, digest)
            raise CaseConflictError("PROJECT_SUBMITTED", "Project has a different submission intention") from None
        await self.requests.reserve(operation.id, actor, command, key, digest)
        jobs = meta_data.tables["git_learning_jobs"]
        previous = await self.job_row(operation.id)
        if previous is not None:
            response = self._response(previous)
            plan = PLAN.validate_json(value(previous, "input", bytes))
            if response is not None:
                await self.requests.complete(command, response)
            await self.session.commit()
            return response or plan
        guards = meta_data.tables["git_learning_guards"]
        await self.session.execute(pg_insert(guards).values(resource_id=command.target_id).on_conflict_do_nothing())
        guard = (
            (
                await self.session.execute(
                    select(guards).where(guards.c.resource_id == command.target_id).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if guard["operation_id"] is not None:
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Retry the unfinished operation first")
        plan = (
            await plan_submission(self, operation, actor, command, service_owner)
            if command.action == SUBMIT
            else await self._plan(operation, actor, command, service_owner)
        )
        if self.preflight_head is not None and plan.head_sha != self.preflight_head:
            raise CaseConflictError("STALE_VERSION", "File changed before reservation")
        if self.preflight_source is not None and (plan.source_commit, plan.source_tree) != self.preflight_source:
            raise CaseConflictError("STALE_VERSION", "Source changed before reservation")
        payload = PLAN.dump_json(plan)
        await self.session.execute(
            insert(jobs).values(
                operation_id=operation.id,
                actor_id=actor,
                resource_id=command.target_id,
                input=payload,
                input_sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
        await self.session.execute(
            update(guards).where(guards.c.resource_id == command.target_id).values(operation_id=operation.id)
        )
        await self._typed_intent(plan, business_digest, now)
        await self.session.commit()
        return plan

    async def workspace(self, repository_id: UUID) -> Mapping[str, object]:
        table = meta_data.tables["git_workspaces"]
        row = (await self.session.execute(select(table).where(table.c.repository_id == repository_id))).mappings().one()
        if row["provider_instance_key"] != self.instance_key:
            raise CaseConflictError("STORAGE_CHANGED", "Repository storage identity changed")
        return row_mapping(row)

    async def _plan(self, operation: Operation, actor: UserId, command: LearningCommand, owner: int) -> GitPlan:
        repository_id, project_id = uuid4(), None
        source_name = source_commit = source_tree = head = None
        reuse_snapshot = False
        branch = "main"
        if command.action.value.startswith("project.") and command.action != START:
            repository_id, branch = await self._editable_project(command.target_id), "work"
            workspace = await self.workspace(repository_id)
            name, head = value(workspace, "name", str), value(workspace, "head_sha", str)
        else:
            case = await self.case_record(command.target_id, published=command.action == START, lock=True)
            if command.action != START:
                await self.gateway.require_organizer(actor, lock=True)
                if command.expected_version != case.version:
                    raise CaseConflictError("STALE_VERSION", "Case version changed")
            if command.action not in {START, PUBLISH}:
                await self.editable(case)
            if command.action in {START, PUBLISH, INIT}:
                source = await self._source(case, command.action)
                source_name, source_commit, source_tree = (
                    value(source, "name", str),
                    value(source, "head_sha", str),
                    value(source, "tree_sha", str),
                )
                if command.action == START:
                    project_id, branch = uuid4(), "work"
                    name = f"prodguild-project-{project_id.hex}"
                elif command.action == PUBLISH and case.current_published_content_id is not None:
                    source = await self._source(case, START)
                    repository_id = value(source, "repository_id", UUID)
                    source_name = name = value(source, "name", str)
                    source_commit, source_tree = value(source, "head_sha", str), value(source, "tree_sha", str)
                    reuse_snapshot = True
                else:
                    name = f"prodguild-{'published' if command.action == PUBLISH else 'reference'}-{repository_id.hex}"
            else:
                existing_repository = await self._file_repository(case, command)
                if existing_repository is None:
                    if command.action != LearningAction.MATERIAL_CREATE:
                        raise CaseNotFoundError
                    return GitPlan(
                        operation_id=operation.id,
                        actor_id=actor,
                        command=command,
                        repository_id=repository_id,
                        repository_name=f"prodguild-materials-{repository_id.hex}",
                        owner_id=owner,
                        head_sha=None,
                        branch=branch,
                    )
                repository_id = existing_repository
                workspace = await self.workspace(repository_id)
                name, head = value(workspace, "name", str), value(workspace, "head_sha", str)
        return GitPlan(
            operation_id=operation.id,
            actor_id=actor,
            command=command,
            repository_id=repository_id,
            repository_name=name,
            owner_id=owner,
            head_sha=head,
            branch=branch,
            project_id=project_id,
            source_name=source_name,
            source_commit=source_commit,
            source_tree=source_tree,
            reuse_snapshot=reuse_snapshot,
        )

    async def _editable_project(self, project_id: UUID) -> UUID:
        table = meta_data.tables["projects"]
        project = (
            (await self.session.execute(select(table).where(table.c.id == project_id).with_for_update()))
            .mappings()
            .one()
        )
        if project["state"] != ProjectState.ACTIVE:
            raise CaseConflictError("PROJECT_SUBMITTED", "Submitted projects cannot be edited")
        return value(project, "repository_id", UUID)

    async def _file_repository(self, case: Case, command: LearningCommand) -> UUID | None:
        if not command.action.value.startswith("reference."):
            return case.materials_repository_id
        references = meta_data.tables["reference_solutions"]
        reference = (
            (await self.session.execute(select(references).where(references.c.case_id == case.id)))
            .mappings()
            .one_or_none()
        )
        if reference is None:
            raise CaseNotFoundError
        if value(reference, "version", int) != command.expected_reference_version:
            raise CaseConflictError("STALE_VERSION", "Reference version changed")
        return value(reference, "repository_id", UUID)

    async def editable(self, case: Case) -> None:
        intents = meta_data.tables["project_start_intents"]
        pending = await self.session.scalar(select(intents.c.operation_id).where(intents.c.case_id == case.id).limit(1))
        if case.frozen_at is not None or pending is not None or await self.gateway.has_projects(case.id):
            raise CaseConflictError("CASE_FROZEN", "Case content is frozen")
        if case.status == CaseStatus.PUBLISHED:
            raise CaseConflictError("CASE_PUBLISHED", "Unpublish before editing files")

    async def _source(self, case: Case, action: LearningAction) -> Mapping[str, object]:
        if action == PUBLISH:
            item = await self.gateway.get_owned(case.owner_id, case.id)
            if item is None:
                raise CaseNotFoundError
            item.metadata.require_publishable()
        if action == START:
            contents = meta_data.tables["case_content_versions"]
            content = (
                (await self.session.execute(select(contents).where(contents.c.id == case.current_published_content_id)))
                .mappings()
                .one()
            )
            source_workspace = dict(await self.workspace(value(content, "published_repository_id", UUID)))
            source_workspace.update(
                head_sha=value(content, "starter_commit_sha", str), tree_sha=value(content, "starter_tree_sha", str)
            )
            return source_workspace
        if case.materials_repository_id is None:
            raise CaseConflictError("CASE_NOT_READY", "Upload materials first")
        if action == PUBLISH:
            references = meta_data.tables["reference_solutions"]
            reference = (
                (await self.session.execute(select(references).where(references.c.case_id == case.id)))
                .mappings()
                .one_or_none()
            )
            if reference is None or reference["ready_reference_id"] is None:
                raise CaseConflictError("CASE_NOT_READY", "Mark the current reference ready first")
            workspace = await self.workspace(value(reference, "repository_id", UUID))
            retained = meta_data.tables["retained_references"]
            ready_sha = await self.session.scalar(
                select(retained.c.commit_id).where(retained.c.id == reference["ready_reference_id"])
            )
            if ready_sha != workspace["head_sha"]:
                raise CaseConflictError("CASE_NOT_READY", "Reference readiness is stale")
        return await self.workspace(case.materials_repository_id)

    async def lock(self, actor: UserId, plan: GitPlan) -> CachedResponse | None:
        operations = meta_data.tables["operations"]
        row = (
            (
                await self.session.execute(
                    select(operations).where(operations.c.id == plan.operation_id).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        operation = operation_from_row(row)
        await self.authorize(actor, plan.command)
        job = await self.job_row(plan.operation_id)
        if job is None:
            raise ValueError("Learning operation has no persisted job")
        response = self._response(job)
        if response is not None:
            await self.session.commit()
            return response
        now = await database_now(self.session)
        if not operation.work.is_due(now):
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Operation is pending; retry the same request")
        work = operation.work.claim(now=now, owner="http-learning", token=uuid4(), policy=POLICY)
        await self.session.execute(
            update(operations).where(operations.c.id == operation.id).values(**work_values(work))
        )
        await self.session.commit()
        if work.state != WorkState.RUNNING:
            raise CaseConflictError("OPERATION_NEEDS_ATTENTION", "Operation needs reconciliation")
        self.claimed = replace(operation, work=work)
        return None

    async def failed(self, plan: GitPlan) -> None:
        await self.session.rollback()
        operation = self.claimed
        if operation is None or operation.work.lease is None:
            raise ValueError("Learning operation has no claimed lease")
        now = await database_now(self.session)
        if operation.work.lease.until > now:
            failed = replace(
                operation,
                work=operation.work.fail(
                    token=operation.work.lease.token, now=now, code=FailureCode.UNAVAILABLE, policy=POLICY
                ),
            )
            await self.operations.save_leased(failed, token=operation.work.lease.token, now=now)
            await self.session.commit()

    @staticmethod
    def _response(row: Mapping[str, object]) -> CachedResponse | None:
        body = optional(row, "response", bytes)
        if body is None:
            return None
        status = value(row, "response_status", int)
        location = optional(row, "response_location", str)
        return CachedResponse(status_code=200 if status == 201 else status, body=body, location=location)

    async def job_row(self, operation_id: UUID) -> Mapping[str, object] | None:
        jobs, results = meta_data.tables["git_learning_jobs"], meta_data.tables["git_learning_results"]
        row = (
            (
                await self.session.execute(
                    select(jobs, results.c.response, results.c.response_status, results.c.response_location)
                    .outerjoin(results, results.c.operation_id == jobs.c.operation_id)
                    .where(jobs.c.operation_id == operation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return row_mapping(row) if row is not None else None

    async def _typed_intent(self, plan: GitPlan, digest: str, now: datetime) -> None:
        if plan.command.action == SUBMIT:
            await submission_intent(self, plan, digest, now)
        elif plan.command.action == START:
            case = await self.case_record(plan.command.target_id)
            contents = meta_data.tables["case_content_versions"]
            content = (
                (await self.session.execute(select(contents).where(contents.c.id == case.current_published_content_id)))
                .mappings()
                .one()
            )
            await self.session.execute(
                insert(meta_data.tables["project_start_intents"]).values(
                    operation_id=plan.operation_id,
                    created_at=now,
                    payload_namespace="prodguild.learning.v1",
                    payload_hash=digest,
                    reserved_project_id=plan.project_id,
                    reserved_repository_id=plan.repository_id,
                    actor_id=plan.actor_id,
                    participant_id=plan.actor_id,
                    case_id=case.id,
                    content_version_id=content["id"],
                    content_version=content["content_version"],
                    starter_repository_id=content["published_repository_id"],
                    starter_retained_reference_id=content["published_retained_reference_id"],
                    provider_instance_key=self.instance_key,
                    service_owner_id=plan.owner_id,
                    repository_slug=plan.repository_name,
                    working_branch="work",
                    starter_commit_sha=plan.source_commit,
                    source_tree_sha=plan.source_tree,
                )
            )
        elif plan.command.action.value.startswith("project."):
            command = plan.command
            file_content = command.content.encode() if command.content is not None else None
            await self.session.execute(
                insert(meta_data.tables["project_file_intents"]).values(
                    operation_id=plan.operation_id,
                    created_at=now,
                    payload_namespace="prodguild.learning.v1",
                    payload_hash=digest,
                    actor_id=plan.actor_id,
                    project_id=command.target_id,
                    repository_id=plan.repository_id,
                    working_branch="work",
                    verb=command.action.value.split(".")[1],
                    path=command.path,
                    expected_head_sha=plan.head_sha,
                    expected_blob_sha=command.sha,
                    message_present=False,
                    commit_message=None,
                    content=file_content,
                    byte_length=len(file_content) if file_content is not None else None,
                    content_sha256=hashlib.sha256(file_content).hexdigest() if file_content is not None else None,
                )
            )

    async def finish(
        self, plan: GitPlan, snapshot: GitSnapshot, files: tuple[GitFile, ...], pull: GitPullRequest | None = None
    ) -> CachedResponse:
        operation = self.claimed
        if operation is None or operation.work.lease is None:
            raise ValueError("Learning operation has no claimed lease")
        now = await database_now(self.session)
        succeeded = replace(
            operation,
            external_id=str(snapshot.repository_id),
            work=operation.work.succeed(token=operation.work.lease.token, now=now),
        )
        if not await self.operations.save_leased(succeeded, token=operation.work.lease.token, now=now):
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Operation lease changed")
        await self.requests.lock(plan.operation_id)
        if plan.command.action.value.startswith("project.") and plan.command.action != START:
            projects = meta_data.tables["projects"]
            await self.session.execute(
                select(projects.c.id).where(projects.c.id == plan.command.target_id).with_for_update()
            )
        else:
            await self.case_record(plan.command.target_id, lock=True)
        await self.authorize(plan.actor_id, plan.command, lock=True)
        response = (
            await finalize_submission(self, plan, snapshot, pull)
            if plan.command.action == SUBMIT
            else await finalize(self, plan, snapshot, files)
        )
        await self.requests.complete(plan.command, response)
        results, guards = meta_data.tables["git_learning_results"], meta_data.tables["git_learning_guards"]
        await self.session.execute(
            insert(results).values(
                operation_id=plan.operation_id,
                response=response.body,
                response_status=response.status_code,
                response_location=response.location,
            )
        )
        await self.session.execute(
            update(guards)
            .where(guards.c.resource_id == plan.command.target_id, guards.c.operation_id == plan.operation_id)
            .values(operation_id=None)
        )
        await self.session.commit()
        return response
