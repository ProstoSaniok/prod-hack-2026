"""A small recoverable learning vertical; external writes follow durable intentions."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from src.application.catalog.management import CaseConflictError
from src.application.common.identity_provider import IdentityProvider
from src.application.operations.idempotency import CachedResponse
from src.application.repository_storage.git import (
    GitFile,
    GitPullRequest,
    GitSnapshot,
    GitStorage,
    TextFile,
    validate_files,
    validate_path,
)
from src.application.repository_storage.ports import StorageError
from src.application.user.entity import UserId


class LearningAction(StrEnum):
    MATERIAL_CREATE = "materials.create"
    MATERIAL_REPLACE = "materials.replace"
    MATERIAL_DELETE = "materials.delete"
    REFERENCE_CREATE = "reference.create"
    REFERENCE_INIT = "reference.init"
    REFERENCE_REPLACE = "reference.replace"
    REFERENCE_DELETE = "reference.delete"
    PUBLISH = "case.publish"
    START = "project.start"
    FILE_CREATE = "project.create"
    FILE_REPLACE = "project.replace"
    FILE_DELETE = "project.delete"
    SUBMIT = "project.submit"


@dataclass(frozen=True, kw_only=True)
class LearningCommand:
    action: LearningAction
    target_id: UUID
    expected_version: int | None = None
    path: str | None = None
    content: str | None = None
    sha: str | None = None
    expected_reference_version: int | None = None
    expected_commit_sha: str | None = None
    summary: str | None = None


@dataclass(frozen=True, kw_only=True)
class GitPlan:
    operation_id: UUID
    actor_id: UserId
    command: LearningCommand
    repository_id: UUID
    repository_name: str
    owner_id: int
    head_sha: str | None
    branch: str
    source_name: str | None = None
    source_commit: str | None = None
    source_tree: str | None = None
    project_id: UUID | None = None
    reuse_snapshot: bool = False
    submission_id: UUID | None = None
    base_commit_sha: str | None = None
    base_tree_sha: str | None = None
    external_repository_id: int | None = None


class LearningStore(Protocol):
    async def authorize(self, actor: UserId, command: LearningCommand) -> None: ...

    async def preflight(self, actor: UserId, command: LearningCommand, key: str) -> tuple[str, str] | None: ...

    async def prepare(
        self, actor: UserId, command: LearningCommand, key: str, service_owner: int
    ) -> GitPlan | CachedResponse: ...

    async def lock(self, actor: UserId, plan: GitPlan) -> CachedResponse | None: ...

    async def finish(
        self, plan: GitPlan, snapshot: GitSnapshot, files: tuple[GitFile, ...], pull: GitPullRequest | None = None
    ) -> CachedResponse: ...

    async def failed(self, plan: GitPlan) -> None: ...


class Learning:
    def __init__(self, identity: IdentityProvider, store: LearningStore, storage: GitStorage) -> None:
        self.identity, self.store, self.storage = identity, store, storage

    async def execute(self, command: LearningCommand, key: str) -> CachedResponse:
        actor = (await self.identity.get_current_user()).id
        await self.store.authorize(actor, command)
        if command.path is not None:
            validate_path(command.path)
        source = await self.store.preflight(actor, command, key)
        if source is not None:
            await self._preflight(command, source)
        service = await self.storage.identity()
        result = await self.store.prepare(actor, command, key, service.id)
        if isinstance(result, CachedResponse):
            return result
        replay = await self.store.lock(actor, result)
        if replay is not None:
            return replay
        try:
            async with asyncio.timeout(90):
                snapshot = await self._apply(result)
                files = await self.storage.files(snapshot.name, snapshot.commit_sha)
                branch = self._retention_branch(result, snapshot)
                await self.storage.retain(snapshot.name, snapshot.commit_sha, branch)
                pull = await self._pull_request(result, snapshot)
        except (StorageError, TimeoutError):
            await self.store.failed(result)
            raise StorageError from None
        return await self.store.finish(result, snapshot, files, pull)

    async def _preflight(self, command: LearningCommand, source: tuple[str, str]) -> None:
        if command.action == LearningAction.SUBMIT:
            current = await self.storage.snapshot(source[0], "work")
            if current.commit_sha != command.expected_commit_sha:
                raise CaseConflictError("COMMIT_CHANGED", "Project HEAD changed; reload before submitting")
            return
        files = await self.storage.files(*source)
        if command.path is None:
            validate_files(tuple(TextFile(file.path, file.content) for file in files))
            return
        existing = next((file for file in files if file.path == command.path), None)
        if (command.action.value.endswith(".create") and existing is not None) or (
            not command.action.value.endswith(".create") and (existing is None or existing.sha != command.sha)
        ):
            raise CaseConflictError("STALE_VERSION", "File changed; reload before saving")
        next_files = [TextFile(file.path, file.content) for file in files if file.path != command.path]
        if command.content is not None:
            next_files.append(TextFile(command.path, command.content.encode()))
        if next_files:
            validate_files(tuple(next_files))

    async def _pull_request(self, plan: GitPlan, snapshot: GitSnapshot) -> GitPullRequest | None:
        if plan.command.action != LearningAction.SUBMIT:
            return None
        if plan.base_commit_sha is None or plan.project_id is None or plan.submission_id is None:
            raise StorageError
        return await self.storage.ensure_pull_request(
            snapshot.name,
            base_branch=f"prodguild/bases/{plan.project_id}",
            base_sha=plan.base_commit_sha,
            head_branch=f"prodguild/submissions/{plan.submission_id}",
            head_sha=snapshot.commit_sha,
            marker=f"prodguild-submission:{plan.submission_id}",
        )

    @staticmethod
    def _retention_branch(plan: GitPlan, snapshot: GitSnapshot) -> str:
        if snapshot.owner_id != plan.owner_id:
            raise StorageError
        if plan.command.action == LearningAction.SUBMIT:
            if plan.submission_id is None:
                raise StorageError
            return f"prodguild/submissions/{plan.submission_id}"
        if plan.command.action == LearningAction.START:
            if plan.project_id is None:
                raise StorageError
            return f"prodguild/bases/{plan.project_id}"
        return f"prodguild/operations/{plan.operation_id}"

    async def _apply(self, plan: GitPlan) -> GitSnapshot:
        marker = f"prodguild-operation:{plan.operation_id}"
        if plan.command.action == LearningAction.SUBMIT:
            return await self._submission_snapshot(plan)
        if plan.source_name is not None:
            if plan.source_commit is None:
                raise StorageError
            source = await self.storage.snapshot(plan.source_name, plan.source_commit)
            if source.tree_sha != plan.source_tree:
                raise StorageError
            if plan.reuse_snapshot:
                return source
            files = await self.storage.files(plan.source_name, plan.source_commit)
            snapshot = await self.storage.ensure_snapshot(
                plan.repository_name,
                marker,
                tuple(TextFile(path=file.path, content=file.content) for file in files),
                branch=plan.branch,
            )
            if snapshot.tree_sha != source.tree_sha:
                raise StorageError
            return snapshot
        command = plan.command
        if command.path is None:
            raise StorageError
        content = command.content.encode("utf-8") if command.content is not None else None
        if plan.head_sha is None:
            if content is None:
                raise StorageError
            return await self.storage.ensure_snapshot(
                plan.repository_name,
                marker,
                (TextFile(path=command.path, content=content),),
                branch=plan.branch,
            )
        return await self.storage.write_file(
            plan.repository_name,
            branch=plan.branch,
            path=command.path,
            content=content,
            expected_blob_sha=command.sha,
            expected_head_sha=plan.head_sha,
            marker=marker,
        )

    async def _submission_snapshot(self, plan: GitPlan) -> GitSnapshot:
        if plan.base_commit_sha is None or plan.head_sha is None:
            raise StorageError
        base = await self.storage.snapshot(plan.repository_name, plan.base_commit_sha)
        # The intention pins C. Recovery must survive later workspace movement.
        head = await self.storage.snapshot(plan.repository_name, plan.head_sha)
        if (
            base.tree_sha != plan.base_tree_sha
            or head.commit_sha != plan.head_sha
            or head.repository_id != plan.external_repository_id
            or base.repository_id != head.repository_id
        ):
            raise StorageError
        return head
