"""Temporary seed API wrapping the real backend.

Set SEEDDATA_API_KEY to a random secret (32+ characters), then run:
  uv run --project src/backend python utils/seeddata/server.py src/backend/config.local.toml
Only this process exposes /seeddata/*, on 127.0.0.1:8081. Stop it after loading.
Normal backend startup is unchanged. Apply migrations before starting.
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.entity import Competency
from src.application.development.entity import (
    SkillGrade,
    SkillGrant,
    SkillGrantEvidenceKind,
    SkillGrantState,
    UserCompetency,
)
from src.application.user.capability import (
    ProductCapability,
    UserCapability,
    UserProfile,
)
from src.application.auth_identity.entity import AuthMethod
from src.application.user.entity import User, UserRole
from src.web_api.configuration import load_configuration
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc
from utils.seeddata.gitea_projection import SeedReviewProjectionProvider


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=6000)


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    email: str = Field(pattern=r"^[a-z0-9-]+\.seed@example\.com$")
    key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal["organizer", "performer", "review_minus", "review_plus", "expert"]
    initial_value: Decimal = Field(ge=0, le=100, allow_inf_nan=False)


class BootstrapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skills: list[SkillInput] = Field(min_length=1, max_length=2000)
    users: list[UserInput] = Field(min_length=1, max_length=12)
    active_skill_slugs: list[str] = Field(min_length=1, max_length=100)


def seed_key() -> str:
    value = os.environ.get("SEEDDATA_API_KEY", "")
    if len(value) < 32:
        raise RuntimeError("Set SEEDDATA_API_KEY to a secret of at least 32 characters")
    return value


async def authorize(x_seeddata_key: Annotated[str, Header()] = "") -> None:
    if not hmac.compare_digest(x_seeddata_key.encode(), seed_key().encode()):
        raise HTTPException(403, "Invalid seed API key")


def identifier(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, "prodguild-seed-v3/" + value)


router = APIRouter(
    prefix="/seeddata", route_class=DishkaRoute, dependencies=[Depends(authorize)]
)


@router.get("/ready")
async def ready() -> dict[str, str]:
    return {"mode": "seeddata-api"}


@router.post("/bootstrap")
async def bootstrap(body: BootstrapInput, session: FromDishka[AsyncSession]) -> dict:
    """Initialize skills/access only; all other data uses ordinary business APIs."""
    now = datetime.now(UTC)
    async with session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(783443123)"))
        if len({s.slug for s in body.skills}) != len(body.skills) or len(
            {s.name for s in body.skills}
        ) != len(body.skills):
            raise HTTPException(422, "Duplicate skill slug or name")
        if not set(body.active_skill_slugs) <= {skill.slug for skill in body.skills}:
            raise HTTPException(422, "Unknown active skill")
        if (
            len({user.id for user in body.users}) != len(body.users)
            or len({user.email for user in body.users}) != len(body.users)
            or len({user.key for user in body.users}) != len(body.users)
        ):
            raise HTTPException(422, "Duplicate seed account")
        skills = {}
        existing = {
            item.name: item
            for item in (await session.scalars(select(Competency))).all()
        }
        for item in body.skills:
            skill = existing.get(item.name)
            if skill is None:
                skill = Competency(
                    id=identifier("skill/" + item.slug),
                    created_at=now,
                    name=item.name,
                    description=item.description,
                )
                session.add(skill)
            elif skill.deleted_at is not None:
                raise HTTPException(409, "Seed skill name belongs to a deleted skill")
            skills[item.slug] = skill.id
        await session.flush()
        identities = meta_data.tables["auth_identities"]
        for item in body.users:
            user = await session.get(User, item.id)
            email = await session.scalar(
                select(identities.c.identifier).where(
                    identities.c.user_id == item.id,
                    identities.c.method == AuthMethod.EMAIL,
                    identities.c.deleted_at.is_(None),
                )
            )
            if (
                user is None
                or user.deleted_at is not None
                or user.role != UserRole.USER
                or email != item.email
            ):
                raise HTTPException(
                    409, "Register the matching seed account through /auth first"
                )
            floor = {
                "organizer": 0,
                "performer": 0,
                "review_minus": 25,
                "review_plus": 50,
                "expert": 75,
            }
            if item.initial_value < floor[item.kind] or (
                item.kind == "organizer" and item.initial_value != 0
            ):
                raise HTTPException(
                    422, "Initial skill value does not match the requested demo grade"
                )
            if (
                await session.scalar(
                    select(UserProfile.id).where(UserProfile.user_id == user.id)
                )
                is not None
            ):
                continue  # Preserve progressed state on rerun.
            session.add(
                UserProfile(
                    id=identifier("profile/" + str(user.id)),
                    created_at=now,
                    user_id=user.id,
                    display_name=item.display_name,
                )
            )
            capability = (
                ProductCapability.ORGANIZER
                if item.kind == "organizer"
                else ProductCapability.PARTICIPANT
            )
            existing_capability = await session.scalar(
                select(UserCapability).where(
                    UserCapability.user_id == user.id,
                    UserCapability.capability == capability,
                )
            )
            if existing_capability is not None:
                if (
                    existing_capability.deleted_at is not None
                    or existing_capability.revoked_at is not None
                ):
                    raise HTTPException(409, "Seed capability has been revoked or deleted")
            else:
                session.add(
                    UserCapability(
                        id=identifier("capability/" + str(user.id)),
                        created_at=now,
                        user_id=user.id,
                        capability=capability,
                    )
                )
            if item.kind == "organizer":
                continue
            grade = SkillGrade(item.kind)
            grants = []
            for slug in body.active_skill_slugs:
                skill_id = skills[slug]
                value = item.initial_value
                session.add(
                    UserCompetency(
                        id=identifier(f"value/{user.id}/{skill_id}"),
                        created_at=now,
                        user_id=user.id,
                        competency_id=skill_id,
                        contribution_sum=value,
                        displayed_value=value,
                        grade=grade,
                    )
                )
                if grade != SkillGrade.PERFORMER:
                    grants.append(
                        SkillGrant(
                            id=identifier(f"grant/{user.id}/{skill_id}"),
                            created_at=now,
                            user_id=user.id,
                            competency_id=skill_id,
                            user_competency_version=1,
                            grade=grade,
                            state=SkillGrantState.CONFIRMED,
                            evidence_kind=SkillGrantEvidenceKind.SEED,
                            evidence="Explicit initial seed access",
                            seed_reference="seeddata-v3:" + item.email,
                            confirmed_at=now,
                        )
                    )
            await session.flush()  # Grants reference the per-user skill aggregate.
            session.add_all(grants)
            await session.flush()
    return {"skills": skills}


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    seed_key()
    app = make_asgi_application(
        make_ioc(load_configuration(args.configuration), SeedReviewProjectionProvider())
    )
    app.include_router(router)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
