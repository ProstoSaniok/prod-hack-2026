"""Seed explicitly enabled local PostgreSQL test accounts."""

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from dishka import AsyncContainer
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.auth_identity.interactor import RegisterByEmail, SignOut
from src.application.auth_identity.ports import AuthRepository
from src.application.common.containers import SecretString
from src.application.development.entity import ReadinessDecision, SkillGrade, SkillGrantEvidenceKind, SkillGrantState
from src.application.user.capability import ProductCapability
from src.web_api.configuration import load_configuration
from src.web_api.ioc.make import make_ioc

_DEMO_COMPETENCIES = (
    ("sql", "SQL", "Работа с SQL"),
    ("python", "Python", "Обработка данных и автоматизация на Python"),
    ("data-analysis", "Анализ данных", "Исследование данных и проверка продуктовых гипотез"),
)
_DEMO_SKILL_VALUES = {
    SkillGrade.PERFORMER: 0,
    SkillGrade.REVIEW_MINUS: 32,
    SkillGrade.REVIEW_PLUS: 58,
    SkillGrade.EXPERT: 80,
}


async def seed(path: Path) -> None:
    config = load_configuration(path)
    if os.environ.get("LOCAL_DEMO_SEED") != "true" or not config.database.url.get_value().startswith("postgresql+"):
        raise ValueError("Demo seed requires explicit LOCAL_DEMO_SEED=true and PostgreSQL")
    password = os.environ["DEMO_ORGANIZER_PASSWORD"]
    container = make_ioc(config)
    try:
        await seed_accounts(container, password)
    finally:
        await container.close()


async def seed_accounts(container: AsyncContainer, password: str) -> None:
    """Populate demo identities idempotently after migrations; caller owns the container."""
    async with container() as scope:
        repository = await scope.get(AuthRepository)
        register = await scope.get(RegisterByEmail)
        sign_out = await scope.get(SignOut)
        identity = await repository.find_identity("organizer.local@example.com")
        if identity is None:
            token = await register.execute("organizer.local@example.com", SecretString(password))
            await sign_out.execute(token)
            identity = await repository.find_identity("organizer.local@example.com")
        if identity is None:
            raise RuntimeError("Demo account was not created")
        session = await scope.get(AsyncSession)
        grants = meta_data.tables["user_capabilities"]
        if (
            await session.scalar(
                select(grants.c.id).where(
                    grants.c.user_id == identity.user_id,
                    grants.c.capability == ProductCapability.ORGANIZER,
                )
            )
            is None
        ):
            await session.execute(
                insert(grants).values(
                    id=uuid4(),
                    user_id=identity.user_id,
                    capability=ProductCapability.ORGANIZER,
                    created_at=datetime.now(UTC),
                    version=1,
                )
            )
        skills = meta_data.tables["competencies"]
        for _, name, description in _DEMO_COMPETENCIES:
            if await session.scalar(select(skills.c.id).where(skills.c.name == name)) is None:
                await session.execute(
                    insert(skills).values(id=uuid4(), name=name, description=description, created_at=datetime.now(UTC))
                )
        await session.commit()
    await seed_roles(container, password)


async def seed_roles(container: AsyncContainer, password: str) -> None:
    for account, grade in (
        ("performer", SkillGrade.PERFORMER),
        ("quality-author", SkillGrade.PERFORMER),
        ("quality-author-mobile", SkillGrade.PERFORMER),
        ("quality-author-desktop", SkillGrade.PERFORMER),
        ("review-minus", SkillGrade.REVIEW_MINUS),
        ("review-plus", SkillGrade.REVIEW_PLUS),
        ("mentor", SkillGrade.EXPERT),
    ):
        async with container() as scope:
            repository = await scope.get(AuthRepository)
            register = await scope.get(RegisterByEmail)
            sign_out = await scope.get(SignOut)
            email = f"{account}.local@example.com"
            identity = await repository.find_identity(email)
            if identity is None:
                token = await register.execute(email, SecretString(password))
                await sign_out.execute(token)
                identity = await repository.find_identity(email)
            if identity is None:
                raise RuntimeError("Demo account was not created")
            session = await scope.get(AsyncSession)
            capabilities = meta_data.tables["user_capabilities"]
            if (
                await session.scalar(
                    select(capabilities.c.id).where(
                        capabilities.c.user_id == identity.user_id,
                        capabilities.c.capability == ProductCapability.PARTICIPANT,
                    )
                )
                is None
            ):
                await session.execute(
                    insert(capabilities).values(
                        id=uuid4(),
                        user_id=identity.user_id,
                        capability=ProductCapability.PARTICIPANT,
                        created_at=datetime.now(UTC),
                        version=1,
                    )
                )
            await _seed_role_skills(session, identity.user_id, account, grade)
            await session.commit()


async def _seed_role_skills(session: AsyncSession, user_id: UUID, account: str, grade: SkillGrade) -> None:
    skills = meta_data.tables["competencies"]
    current = meta_data.tables["user_competencies"]
    grants = meta_data.tables["skill_grants"]
    for key, name, _ in _DEMO_COMPETENCIES:
        competency_id = await session.scalar(
            select(skills.c.id).where(skills.c.name == name, skills.c.deleted_at.is_(None))
        )
        if competency_id is None:
            continue
        # Existing aggregates include soft-deleted rows and retain all earned evidence.
        if (
            await session.scalar(
                select(current.c.id).where(current.c.user_id == user_id, current.c.competency_id == competency_id)
            )
            is not None
        ):
            continue
        value = 80 if key == "sql" else _DEMO_SKILL_VALUES[grade]
        await session.execute(
            insert(current).values(
                id=uuid4(),
                user_id=user_id,
                competency_id=competency_id,
                contribution_sum=value,
                displayed_value=value,
                grade=grade,
                version=1,
                created_at=datetime.now(UTC),
            )
        )
        if grade != SkillGrade.PERFORMER:
            await session.execute(
                insert(grants).values(
                    id=uuid4(),
                    user_id=user_id,
                    competency_id=competency_id,
                    user_competency_version=1,
                    grade=grade,
                    state=SkillGrantState.CONFIRMED,
                    evidence_kind=SkillGrantEvidenceKind.SEED,
                    evidence="Local demo seed",
                    seed_reference=f"local-demo:{account}:{key}:v1",
                    version=1,
                    confirmed_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                    readiness_decision=ReadinessDecision.CONFIRMED,
                    confirmed_by_grade=SkillGrade.EXPERT,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration", type=Path)
    args = parser.parse_args()
    asyncio.run(seed(args.configuration))


if __name__ == "__main__":
    main()
