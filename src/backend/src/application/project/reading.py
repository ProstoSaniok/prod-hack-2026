import json
from typing import Literal, Protocol
from uuid import UUID

from src.application.catalog.management import CaseNotFoundError
from src.application.common.identity_provider import IdentityProvider
from src.application.project.snapshot_reading import (
    PinnedSource,
    SnapshotReads,
    SnapshotTooLargeError,
    SubmissionSide,
)
from src.application.repository_storage.git import GitStorage, validate_path
from src.application.user.entity import UserId

__all__ = ["FileKind", "LearningReader", "LearningReads", "SnapshotTooLargeError", "SubmissionSide"]

type FileKind = Literal["materials", "reference", "published", "project"]
type CatalogDifficulty = Literal["review_minus", "review_plus", "expert"]


class LearningReader(Protocol):
    async def catalogue(
        self,
        *,
        limit: int,
        offset: int,
        q: str = "",
        competency_ids: tuple[UUID, ...] = (),
        difficulty: CatalogDifficulty | None = None,
    ) -> bytes: ...
    async def case(self, case_id: UUID) -> bytes: ...
    async def projects(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def project(self, actor: UserId, project_id: UUID) -> bytes: ...
    async def submissions(self, actor: UserId, *, limit: int, offset: int) -> bytes: ...
    async def submission(self, actor: UserId, submission_id: UUID) -> bytes: ...
    async def submission_source(self, actor: UserId, submission_id: UUID) -> tuple[str, str, str]: ...
    async def reference(self, actor: UserId, case_id: UUID) -> bytes: ...
    async def file_source(self, actor: UserId, target_id: UUID, kind: FileKind) -> tuple[str, str]: ...


class LearningReads:
    def __init__(self, identity: IdentityProvider, reader: LearningReader, storage: GitStorage) -> None:
        self.identity, self.reader, self.storage = identity, reader, storage
        self.snapshots = SnapshotReads(storage)

    async def catalogue(
        self,
        limit: int,
        offset: int,
        *,
        q: str = "",
        competency_ids: tuple[UUID, ...] = (),
        difficulty: CatalogDifficulty | None = None,
    ) -> bytes:
        await self.identity.get_current_user()
        return await self.reader.catalogue(
            limit=limit, offset=offset, q=q, competency_ids=competency_ids, difficulty=difficulty
        )

    async def case(self, case_id: UUID) -> bytes:
        await self.identity.get_current_user()
        return await self.reader.case(case_id)

    async def projects(self, limit: int, offset: int) -> bytes:
        return await self.reader.projects((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def project(self, project_id: UUID) -> bytes:
        return await self.reader.project((await self.identity.get_current_user()).id, project_id)

    async def submissions(self, limit: int, offset: int) -> bytes:
        return await self.reader.submissions((await self.identity.get_current_user()).id, limit=limit, offset=offset)

    async def submission(self, submission_id: UUID) -> bytes:
        return await self.reader.submission((await self.identity.get_current_user()).id, submission_id)

    async def submission_files(
        self,
        submission_id: UUID,
        *,
        side: SubmissionSide = "head",
        path: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> bytes:
        return await self.snapshots.files(
            await self._submission_source(submission_id), side=side, path=path, limit=limit, offset=offset
        )

    async def submission_snapshot(self, submission_id: UUID) -> bytes:
        return await self.snapshots.snapshot(await self._submission_source(submission_id))

    async def submission_diff(self, submission_id: UUID, limit: int, offset: int, path: str | None = None) -> bytes:
        return await self.snapshots.diff(await self._submission_source(submission_id), limit, offset, path)

    async def _submission_source(self, submission_id: UUID) -> PinnedSource:
        name, base, head = await self.reader.submission_source(
            (await self.identity.get_current_user()).id, submission_id
        )
        return PinnedSource(submission_id=submission_id, name=name, base_sha=base, head_sha=head)

    async def reference(self, case_id: UUID) -> bytes:
        return await self.reader.reference((await self.identity.get_current_user()).id, case_id)

    async def files(self, target_id: UUID, kind: FileKind, path: str | None = None) -> bytes:
        actor = (await self.identity.get_current_user()).id
        name, commit = await self.reader.file_source(actor, target_id, kind)
        if path is not None:
            validate_path(path)
        files = await self.storage.files(name, commit)
        if path is not None:
            file = next((file for file in files if file.path == path), None)
            if file is None:
                raise CaseNotFoundError
            return json.dumps(
                {
                    "path": file.path,
                    "sha": file.sha,
                    "size": file.size,
                    "commit_sha": commit,
                    "content": file.content.decode("utf-8"),
                },
                ensure_ascii=False,
            ).encode()
        return json.dumps([
            {
                "path": file.path,
                "name": file.path.rsplit("/", 1)[-1],
                "type": "file",
                "sha": file.sha,
                "size": file.size,
                "commit_sha": commit,
            }
            for file in files
        ]).encode()
