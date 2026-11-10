from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning import DefaultLearningStore
from src.adapters.data_gateways.learning_views import encode
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.case_requests import CaseRequestJournal
from src.adapters.operations.sql_records import value
from src.adapters.operations.sql_work import database_now
from src.application.catalog.entity import CaseId, CaseStatus
from src.application.catalog.management import CaseConflictError, CaseNotFoundError
from src.application.operations.idempotency import (
    CachedResponse,
    ObjectShape,
    RequestIdempotency,
    RequestScope,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)
from src.application.project.learning import LearningAction, LearningCommand
from src.application.project.reading import CatalogDifficulty, FileKind
from src.application.user.entity import UserId


class DefaultLearningReader:
    def __init__(self, session: AsyncSession, store: DefaultLearningStore) -> None:
        self.session, self.store, self.views = session, store, store.views

    async def catalogue(
        self,
        *,
        limit: int,
        offset: int,
        q: str = "",
        competency_ids: tuple[UUID, ...] = (),
        difficulty: CatalogDifficulty | None = None,
    ) -> bytes:
        if offset > 9_223_372_036_854_775_807:
            return b"[]"
        cases, contents, skills = (
            meta_data.tables[name] for name in ("cases", "case_content_versions", "case_content_competencies")
        )
        query = (
            select(cases.c.id)
            .join(contents, contents.c.id == cases.c.current_published_content_id)
            .where(
                cases.c.status == CaseStatus.PUBLISHED,
                cases.c.deleted_at.is_(None),
                cases.c.current_published_content_id.is_not(None),
            )
        )
        if q.strip():
            title = func.replace(func.lower(contents.c.title), "ё", "е")
            query = query.where(title.contains(q.strip().lower().replace("ё", "е"), autoescape=True))
        if competency_ids:
            query = query.where(
                select(skills.c.id)
                .where(skills.c.content_version_id == contents.c.id, skills.c.competency_id.in_(competency_ids))
                .exists()
            )
        if difficulty:
            maximum = (
                select(func.max(skills.c.required_value))
                .where(skills.c.content_version_id == contents.c.id)
                .scalar_subquery()
            )
            if difficulty == "expert":
                query = query.where(maximum >= 75)
            elif difficulty == "review_plus":
                query = query.where(maximum >= 50, maximum < 75)
            else:
                query = query.where(maximum < 50)
        ids = await self.session.scalars(
            query.order_by(cases.c.created_at.desc(), cases.c.id).limit(limit).offset(offset)
        )
        return b"[" + b",".join([await self.views.published(CaseId(case_id)) for case_id in ids]) + b"]"

    async def case(self, case_id: UUID) -> bytes:
        return await self.views.published(CaseId(case_id))

    async def projects(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        await self.store.participant(actor)
        if offset > 9_223_372_036_854_775_807:
            return b"[]"
        projects = meta_data.tables["projects"]
        ids = await self.session.scalars(
            select(projects.c.id)
            .where(
                projects.c.participant_id == actor,
                projects.c.deleted_at.is_(None),
                projects.c.content_version_id.is_not(None),
            )
            .order_by(projects.c.created_at.desc(), projects.c.id)
            .limit(limit)
            .offset(offset)
        )
        return encode([await self.views.project(actor, project_id) for project_id in ids])

    async def project(self, actor: UserId, project_id: UUID) -> bytes:
        await self.store.authorize(actor, LearningCommand(action=LearningAction.FILE_REPLACE, target_id=project_id))
        return encode(await self.views.project(actor, project_id))

    async def submissions(self, actor: UserId, *, limit: int, offset: int) -> bytes:
        await self.store.participant(actor)
        if offset > 9_223_372_036_854_775_807:
            return b"[]"
        submissions, projects = meta_data.tables["submissions"], meta_data.tables["projects"]
        ids = await self.session.scalars(
            select(submissions.c.id)
            .join(projects, projects.c.id == submissions.c.project_id)
            .where(
                submissions.c.author_id == actor,
                projects.c.participant_id == actor,
                submissions.c.deleted_at.is_(None),
                projects.c.deleted_at.is_(None),
                submissions.c.operation_id.is_not(None),
            )
            .order_by(submissions.c.created_at.desc(), submissions.c.id)
            .limit(limit)
            .offset(offset)
        )
        return b"[" + b",".join([await self.views.submission(actor, submission_id) for submission_id in ids]) + b"]"

    async def submission(self, actor: UserId, submission_id: UUID) -> bytes:
        await self.store.participant(actor)
        return await self.views.submission(actor, submission_id, include_priority=True)

    async def submission_source(self, actor: UserId, submission_id: UUID) -> tuple[str, str, str]:
        await self.store.participant(actor)
        row = await self.views.submission_record(actor, submission_id)
        workspace = await self.store.workspace(value(row, "repository_id", UUID))
        return value(workspace, "name", str), value(row, "base_commit_sha", str), value(row, "commit_sha", str)

    async def reference(self, actor: UserId, case_id: UUID) -> bytes:
        await self.store.authorize(actor, LearningCommand(action=LearningAction.REFERENCE_INIT, target_id=case_id))
        return encode(await self.views.reference(case_id))

    async def file_source(self, actor: UserId, target_id: UUID, kind: FileKind) -> tuple[str, str]:
        if kind == "project":
            await self.store.authorize(actor, LearningCommand(action=LearningAction.FILE_REPLACE, target_id=target_id))
            projects = meta_data.tables["projects"]
            repository = await self.session.scalar(select(projects.c.repository_id).where(projects.c.id == target_id))
        elif kind == "published":
            case = await self.store.case_record(target_id, published=True)
            contents = meta_data.tables["case_content_versions"]
            content = (
                (await self.session.execute(select(contents).where(contents.c.id == case.current_published_content_id)))
                .mappings()
                .one()
            )
            workspace = await self.store.workspace(value(content, "published_repository_id", UUID))
            return value(workspace, "name", str), value(content, "starter_commit_sha", str)
        else:
            await self.store.authorize(
                actor, LearningCommand(action=LearningAction.REFERENCE_INIT, target_id=target_id)
            )
            if kind == "materials":
                repository = (await self.store.case_record(target_id)).materials_repository_id
            else:
                references = meta_data.tables["reference_solutions"]
                repository = await self.session.scalar(
                    select(references.c.repository_id).where(references.c.case_id == target_id)
                )
        if repository is None:
            raise CaseNotFoundError
        if not isinstance(repository, UUID):
            raise TypeError("Invalid learning repository identity")
        workspace = await self.store.workspace(repository)
        return value(workspace, "name", str), value(workspace, "head_sha", str)

    async def set_ready(
        self,
        actor: UserId,
        case_id: UUID,
        expected_case_version: int,
        expected_version: int,
        expected_commit_sha: str,
        *,
        ready: bool,
        key: str,
    ) -> bytes:
        await self.store.authorize(actor, LearningCommand(action=LearningAction.REFERENCE_INIT, target_id=case_id))
        path = f"/api/v1/admin/cases/{case_id}/reference-solution"
        # Explicit private-reference shape, never a generic/default-allow capture policy.
        string = ScalarShape(kinds=frozenset({ScalarKind.STRING}))
        shape = ObjectShape(
            fields=(
                *(
                    ResponseField(name=name, shape=string, required=True)
                    for name in ("id", "case_id", "head_commit_sha", "created_at", "updated_at")
                ),
                ResponseField(name="version", shape=ScalarShape(kinds=frozenset({ScalarKind.INTEGER})), required=True),
                ResponseField(name="ready", shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN})), required=True),
                ResponseField(
                    name="ready_commit_sha",
                    shape=ScalarShape(kinds=frozenset({ScalarKind.STRING, ScalarKind.NULL})),
                    required=True,
                ),
                ResponseField(name="repository", shape=ScalarShape(kinds=frozenset({ScalarKind.NULL})), required=True),
            )
        )
        journal = CaseRequestJournal(self.session, await database_now(self.session))
        handler = RequestIdempotency(
            clock=journal,
            response_policies=(
                ResponseCapturePolicy(method="PATCH", path=path, allowed_statuses=frozenset({200}), body_shape=shape),
            ),
        )
        record = await handler.begin(
            journal,
            RequestScope(actor_id=actor, method="PATCH", path=path, key=key),
            encode({
                "expected_case_version": expected_case_version,
                "expected_version": expected_version,
                "expected_commit_sha": expected_commit_sha,
                "ready": ready,
            }),
        )
        if isinstance(record, CachedResponse):
            return record.body
        case = await self.store.gateway.get_owned(actor, CaseId(case_id), lock=True)
        if case is None:
            raise CaseNotFoundError
        await self.store.gateway.require_organizer(actor, lock=True)
        await self.store.editable(case.case)
        references, retained = meta_data.tables["reference_solutions"], meta_data.tables["retained_references"]
        reference = (
            (await self.session.execute(select(references).where(references.c.case_id == case_id)))
            .mappings()
            .one_or_none()
        )
        if reference is None:
            raise CaseNotFoundError
        workspace = await self.store.workspace(value(reference, "repository_id", UUID))
        if (
            case.case.version != expected_case_version
            or reference["version"] != expected_version
            or workspace["head_sha"] != expected_commit_sha
        ):
            raise CaseConflictError("STALE_VERSION", "Case or reference changed")
        reference_id = await self.session.scalar(
            select(retained.c.id)
            .where(retained.c.repository_id == reference["repository_id"], retained.c.commit_id == expected_commit_sha)
            .order_by(retained.c.created_at, retained.c.id)
            .limit(1)
        )
        if reference_id is None:
            raise CaseConflictError("CASE_NOT_READY", "Reference commit is not retained")
        now = await database_now(self.session)
        await self.session.execute(
            update(references)
            .where(references.c.id == reference["id"])
            .values(
                version=references.c.version + 1,
                updated_at=now,
                ready_reference_id=reference_id if ready else None,
                ready_at=now if ready else None,
            )
        )
        case.case.version += 1
        case.case.updated_at = now
        case.case.current_published_content_id = None
        await self.session.flush()
        body = encode(await self.views.reference(case_id))
        if not await handler.complete(journal, record, status_code=200, body=body):
            raise CaseConflictError("OPERATION_IN_PROGRESS", "Request capture expired")
        await self.session.commit()
        return body
