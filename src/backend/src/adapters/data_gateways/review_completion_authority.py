"""Narrow publication continuity for a confirmed-minus assignment across promotion."""

import json
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.review_matching import ReviewMatching, ReviewerSkill
from src.adapters.data_gateways.review_revision_writes import publication_skill
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.catalog.management import CaseConflictError
from src.application.development.entity import SkillGrade, SkillGrant, SkillGrantEvidenceKind, SkillGrantState
from src.application.development.policy import require_solution_reviewer
from src.application.development.policy_types import (
    POLICY_VERSION,
    CaseSkillInput,
    Grade,
    Grant,
    PolicyViolationError,
    SkillAuthorityInput,
)
from src.application.review.finalization import PublicationSkill


class SqlCompletionAuthority:
    """Caller already holds sorted user locks and revalidated current all-skill rights."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self, assignment: dict[str, object], subject: dict[str, object], rights: dict[UUID, ReviewerSkill]
    ) -> tuple[PublicationSkill, ...]:
        requirements = await ReviewMatching(self.session).requirements(value(subject, "id", UUID))
        result: list[PublicationSkill] = []
        for row in requirements:
            requirement = row_mapping(row)
            right = rights[value(requirement, "competency_id", UUID)]
            if not assignment["supervised"] and right.grant.state == SkillGrantState.PROVISIONAL:
                result.append(await self._promoted(assignment, subject, requirement, right))
            else:
                result.append(publication_skill(requirement, right))
        return tuple(result)

    async def _promoted(
        self,
        assignment: dict[str, object],
        subject: dict[str, object],
        requirement: dict[str, object],
        current: ReviewerSkill,
    ) -> PublicationSkill:
        bindings = meta_data.tables["assignment_competencies"]
        binding = (
            (
                await self.session.execute(
                    select(bindings)
                    .where(
                        bindings.c.assignment_id == assignment["id"],
                        bindings.c.competency_id == requirement["competency_id"],
                    )
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .one()
        )
        grants = meta_data.tables["skill_grants"]
        old = await self.session.scalar(
            select(SkillGrant)
            .where(grants.c.id == binding["grant_id"])
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        require_promotion_link(assignment, row_mapping(binding), old, current)
        pinned = PublicationSkill(
            competency_id=value(requirement, "competency_id", UUID),
            weight=value(requirement, "weight", Decimal),
            reviewer_value=binding["value"],
            reviewer_grade=SkillGrade.REVIEW_MINUS,
            grant_id=binding["grant_id"],
            grant_version=binding["grant_version"],
            competency_version=binding["user_competency_version"],
            grant_state=SkillGrantState.CONFIRMED,
        )
        # Recheck the historical lower-grade admission too. The replacement's
        # higher grade cannot authorize a case the original grant could not.
        try:
            require_solution_reviewer(
                current.competency.user_id,
                value(subject, "author_id", UUID),
                (
                    CaseSkillInput(
                        skill_id=pinned.competency_id,
                        required_value=value(requirement, "required_value", Decimal),
                        author_grade=Grade(value(requirement, "author_grade", SkillGrade).value),
                    ),
                ),
                (
                    SkillAuthorityInput(
                        user_id=current.competency.user_id,
                        skill_id=pinned.competency_id,
                        value=pinned.reviewer_value,
                        grade=Grade.REVIEW_MINUS,
                        grant=Grant.CONFIRMED,
                    ),
                ),
            )
        except PolicyViolationError:
            raise CaseConflictError("SUPERVISION_REQUIRED", "Original confirmed authority is insufficient") from None
        return pinned


def require_promotion_link(
    assignment: dict[str, object], binding: dict[str, object], old: SkillGrant | None, current: ReviewerSkill
) -> None:
    new = current.grant
    try:
        parsed = json.loads(new.evidence)
    except (ValueError, TypeError):
        raise CaseConflictError("SUPERVISION_REQUIRED", "Promotion provenance is unavailable") from None
    if not isinstance(parsed, dict):
        raise CaseConflictError("SUPERVISION_REQUIRED", "Promotion provenance is unavailable")
    evidence = cast(dict[str, object], parsed)
    candidate_version = evidence.get("candidate_version")
    claimed = value(assignment, "created_at", datetime)
    if (
        old is None
        or binding["deleted_at"] is not None
        or binding["grade"] != SkillGrade.REVIEW_MINUS
        or binding["grant_state"] != SkillGrantState.CONFIRMED
        or binding["probation_id"] is not None
        or old.user_id != assignment["reviewer_id"]
        or old.user_id != new.user_id
        or old.competency_id != new.competency_id
        or old.grade != SkillGrade.REVIEW_MINUS
        or old.deleted_at is not None
        or old.state != SkillGrantState.REVOKED
        or old.confirmed_at is None
        or not old.created_at <= old.confirmed_at <= claimed <= new.created_at
        or old.revoked_at != new.created_at
        or old.version != value(binding, "grant_version", int) + 1
        or new.grade != SkillGrade.REVIEW_PLUS
        or new.evidence_kind != SkillGrantEvidenceKind.PROGRESSION
        or new.state != SkillGrantState.PROVISIONAL
        or new.confirmed_at is not None
        or new.readiness_assessment_id is not None
        or new.seed_reference is not None
        or new.deleted_at is not None
        or new.revoked_at is not None
        or current.probation_id is None
        or current.authority.grant != Grant.SUPERVISED
        or evidence.get("policy_version") != POLICY_VERSION
        or evidence.get("user_id") != str(new.user_id)
        or evidence.get("skill_id") != str(new.competency_id)
        or evidence.get("target_grade") != Grade.REVIEW_PLUS
        or evidence.get("previous_grant_id") != str(old.id)
        or type(evidence.get("previous_grant_version")) is not int
        or evidence.get("previous_grant_version") != binding["grant_version"]
        or type(candidate_version) is not int
        or not old.user_competency_version <= value(binding, "user_competency_version", int) <= candidate_version
        or new.user_competency_version != candidate_version + 1
        or current.competency.version < new.user_competency_version
    ):
        raise CaseConflictError("SUPERVISION_REQUIRED", "Original authority lacks a coherent promotion successor")
