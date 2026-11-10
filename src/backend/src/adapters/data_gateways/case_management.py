from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.auth_identity.errors import ForbiddenError
from src.application.catalog.entity import Case, CaseCompetency, CaseCompetencyId, CaseId, CaseStatus, Competency
from src.application.catalog.management import (
    CaseConflictError,
    CaseMetadata,
    CaseSkill,
    CaseValidationError,
    ManagedCase,
)
from src.application.user.capability import ProductCapability
from src.application.user.entity import UserId


class DefaultCaseGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def require_organizer(self, actor_id: UserId, *, lock: bool = False) -> None:
        grants, users = meta_data.tables["user_capabilities"], meta_data.tables["users"]
        statement = (
            select(grants.c.id)
            .join(users, users.c.id == grants.c.user_id)
            .where(
                grants.c.user_id == actor_id,
                grants.c.capability == ProductCapability.ORGANIZER,
                grants.c.revoked_at.is_(None),
                grants.c.deleted_at.is_(None),
                users.c.deleted_at.is_(None),
            )
        )
        if lock:
            statement = statement.with_for_update(read=True)
        if await self.session.scalar(statement) is None:
            raise ForbiddenError

    async def get_owned(self, actor_id: UserId, case_id: CaseId, *, lock: bool = False) -> ManagedCase | None:
        table = meta_data.tables["cases"]
        statement = select(Case).where(table.c.id == case_id, table.c.owner_id == actor_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        case = await self.session.scalar(statement)
        if case is None:
            return None
        if lock:
            guards = meta_data.tables["git_learning_guards"]
            if (
                await self.session.scalar(select(guards.c.operation_id).where(guards.c.resource_id == case_id))
                is not None
            ):
                raise CaseConflictError("OPERATION_IN_PROGRESS", "Finish the pending Git operation before editing")
        return (await self._with_skills([case]))[0]

    async def list_owned(
        self, actor_id: UserId, *, limit: int, offset: int, status: CaseStatus | None
    ) -> list[ManagedCase]:
        table = meta_data.tables["cases"]
        statement = select(Case).where(table.c.owner_id == actor_id, table.c.deleted_at.is_(None))
        if status is not None:
            statement = statement.where(table.c.status == status)
        cases = list(
            await self.session.scalars(
                statement.order_by(table.c.created_at.desc(), table.c.id.asc()).limit(limit).offset(offset)
            )
        )
        return await self._with_skills(cases)

    async def _with_skills(self, cases: list[Case]) -> list[ManagedCase]:
        if not cases:
            return []
        table = meta_data.tables["case_competencies"]
        skills: dict[CaseId, list[CaseSkill]] = {case.id: [] for case in cases}
        for row in await self.session.scalars(
            select(CaseCompetency)
            .where(table.c.case_id.in_(skills), table.c.deleted_at.is_(None))
            .order_by(table.c.competency_id.asc())
            .execution_options(populate_existing=True)
        ):
            skills[row.case_id].append(
                CaseSkill(
                    competency_id=row.competency_id,
                    required_value=row.required_value,
                    weight=row.weight,
                )
            )
        return [
            ManagedCase(
                case=case,
                metadata=CaseMetadata(
                    title=case.title,
                    description=case.description,
                    expected_result=case.expected_result,
                    competencies=tuple(skills[case.id]),
                ),
            )
            for case in cases
        ]

    async def validate_skills(self, skills: tuple[CaseSkill, ...]) -> None:
        ids = {skill.competency_id for skill in skills}
        table = meta_data.tables["competencies"]
        found = {
            row.id
            for row in await self.session.scalars(
                select(Competency)
                .where(table.c.id.in_(ids), table.c.deleted_at.is_(None))
                .order_by(table.c.id)
                .with_for_update(read=True)
            )
        }
        if found != ids:
            raise CaseValidationError("Unknown or deleted competency")

    async def has_projects(self, case_id: CaseId) -> bool:
        table = meta_data.tables["projects"]
        intents = meta_data.tables["project_start_intents"]
        return (
            await self.session.scalar(select(table.c.id).where(table.c.case_id == case_id).limit(1)) is not None
            or await self.session.scalar(select(intents.c.operation_id).where(intents.c.case_id == case_id).limit(1))
            is not None
        )

    async def create_if_absent(self, case: Case, skills: tuple[CaseSkill, ...]) -> bool:
        table = meta_data.tables["cases"]
        inserted = await self.session.scalar(
            pg_insert(table)
            .values(
                id=case.id,
                created_at=case.created_at,
                updated_at=case.updated_at,
                owner_id=case.owner_id,
                title=case.title,
                description=case.description,
                expected_result=case.expected_result,
                status=case.status,
                version=case.version,
            )
            .on_conflict_do_nothing(index_elements=[table.c.id])
            .returning(table.c.id)
        )
        if inserted is None:
            return False
        await self._save_skills(case, skills, creating=True)
        return True

    async def save(self, case: Case, skills: tuple[CaseSkill, ...] | None, *, creating: bool = False) -> None:
        self.session.add(case)
        await self.session.flush()
        await self._save_skills(case, skills, creating=creating)

    async def _save_skills(self, case: Case, skills: tuple[CaseSkill, ...] | None, *, creating: bool) -> None:
        if skills is not None:
            table = meta_data.tables["case_competencies"]
            if not creating:
                await self.session.execute(delete(table).where(table.c.case_id == case.id))
            self.session.add_all([
                CaseCompetency(
                    id=CaseCompetencyId(uuid4()),
                    created_at=case.updated_at or case.created_at,
                    case_id=case.id,
                    competency_id=skill.competency_id,
                    required_value=skill.required_value,
                    weight=skill.weight,
                )
                for skill in skills
            ])
            await self.session.flush()
