from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.entity import Competency
from src.application.development.entity import UserCompetency
from src.application.development.list_my_skills import MySkill, MySkillsUnavailableError
from src.application.user.entity import UserId


class DefaultMySkillsReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_skills(self, owner_id: UserId, *, limit: int, offset: int) -> list[MySkill]:
        skills = meta_data.tables["user_competencies"]
        competencies = meta_data.tables["competencies"]
        statement = (
            select(UserCompetency, Competency)
            .join(Competency, competencies.c.id == skills.c.competency_id)
            .where(
                skills.c.user_id == owner_id,
                skills.c.deleted_at.is_(None),
                competencies.c.deleted_at.is_(None),
            )
            .order_by(skills.c.created_at.desc(), skills.c.competency_id.asc())
            .limit(limit)
            .offset(offset)
        )
        try:
            with self.session.no_autoflush:
                rows = (await self.session.execute(statement)).all()
        except SQLAlchemyError:
            raise MySkillsUnavailableError from None
        return [
            MySkill(
                competency_id=skill.competency_id,
                name=competency.name,
                grade=skill.grade,
                displayed_value=skill.displayed_value,
            )
            for skill, competency in rows
        ]
