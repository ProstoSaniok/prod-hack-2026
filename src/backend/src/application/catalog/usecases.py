from datetime import UTC, datetime
from uuid import uuid4

from src.application.catalog.entity import Case, CaseId, CaseStatus
from src.application.catalog.management import (
    CaseAccess,
    CaseChanges,
    CaseConflictError,
    CaseDifficulty,
    CaseGateway,
    CaseMetadata,
    CaseNotFoundError,
    CaseValidationError,
    ManagedCase,
)
from src.application.common.interactor import to_interactor


@to_interactor
class CreateCase:
    access: CaseAccess
    gateway: CaseGateway

    async def execute(
        self, metadata: CaseMetadata, difficulty: CaseDifficulty | None = None, case_id: CaseId | None = None
    ) -> tuple[ManagedCase, bool]:
        actor = await self.access.authorize()
        await self.gateway.require_organizer(actor, lock=True)
        if case_id is not None:
            existing = await self.gateway.get_owned(actor, case_id)
            if existing is not None:
                if existing.case.deleted_at is not None:
                    raise CaseNotFoundError
                return existing, False
        self.access.check_difficulty(metadata, difficulty)
        await self.gateway.validate_skills(metadata.competencies)
        now = datetime.now(UTC)
        case = Case(
            id=case_id or CaseId(uuid4()),
            created_at=now,
            updated_at=now,
            owner_id=actor,
            title=metadata.title,
            description=metadata.description,
            expected_result=metadata.expected_result,
        )
        if await self.gateway.create_if_absent(case, metadata.competencies):
            return ManagedCase(case=case, metadata=metadata), True
        existing = await self.gateway.get_owned(actor, case.id)
        if existing is None or existing.case.deleted_at is not None:
            raise CaseNotFoundError
        return existing, False


@to_interactor
class UpdateCase:
    access: CaseAccess
    gateway: CaseGateway

    async def execute(self, case_id: CaseId, changes: CaseChanges) -> ManagedCase:
        item = await self.access.lock(case_id, changes.expected_version)
        case, old = item.case, item.metadata
        if changes.status == CaseStatus.PUBLISHED:
            raise CaseConflictError(
                "CASE_NOT_READY", "Publication requires a verified material snapshot and ready reference"
            )
        if not changes.changes_content and changes.status is None and changes.difficulty is None:
            raise CaseValidationError("At least one change is required")
        if changes.changes_content:
            await self.access.require_editable(case)
            if case.status == CaseStatus.PUBLISHED:
                raise CaseConflictError("CASE_PUBLISHED", "Unpublish the case before changing its content")
        metadata = CaseMetadata(
            title=changes.title if changes.title is not None else old.title,
            description=changes.description if changes.description is not None else old.description,
            expected_result=changes.expected_result if changes.expected_result is not None else old.expected_result,
            competencies=changes.competencies if changes.competencies is not None else old.competencies,
        )
        self.access.check_difficulty(metadata, changes.difficulty)
        if changes.competencies is not None:
            await self.gateway.validate_skills(changes.competencies)
        case.title, case.description, case.expected_result = (
            metadata.title,
            metadata.description,
            metadata.expected_result,
        )
        if changes.status is not None:
            case.status = changes.status
        if changes.changes_content:
            case.current_published_content_id = None
        case.version += 1
        case.updated_at = datetime.now(UTC)
        await self.gateway.save(case, changes.competencies)
        return ManagedCase(case=case, metadata=metadata)


@to_interactor
class DeleteCase:
    access: CaseAccess
    gateway: CaseGateway

    async def execute(self, case_id: CaseId, expected_version: int) -> None:
        item = await self.access.lock(case_id, expected_version)
        await self.access.require_editable(item.case)
        if item.case.status == CaseStatus.PUBLISHED:
            raise CaseConflictError("CASE_PUBLISHED", "Archive the case before deleting it")
        item.case.deleted_at = item.case.updated_at = datetime.now(UTC)
        item.case.version += 1
        await self.gateway.save(item.case, None)
