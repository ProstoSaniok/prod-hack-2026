"""Sequential grade entry in the publication/readiness caller's transaction."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.grade_evidence import GradeCandidate, SqlGradeEvidence, gate_record
from src.adapters.data_gateways.learning_views import encode
from src.adapters.data_gateways.tables import meta_data
from src.application.development.entity import ReadinessDecision, SkillGrade, SkillGrantEvidenceKind, SkillGrantState
from src.application.development.policy import require_grade_entry, start_probation
from src.application.development.policy_types import Grade, GradeGateEvidence, PolicyViolationError
from src.application.user.entity import User


class SqlGradeAdvancement:
    """Caller holds sorted participating-user locks; never commit or acquire wallets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.evidence = SqlGradeEvidence(session)

    async def advance(self, user_id: UUID, skill_id: UUID, now: datetime) -> None:
        user = await self.session.get(User, user_id, populate_existing=True)
        if user is None or user.deleted_at is not None:
            return
        candidate = await self.evidence.candidate(user, skill_id)
        target = {Grade.PERFORMER: Grade.REVIEW_MINUS, Grade.REVIEW_MINUS: Grade.REVIEW_PLUS}.get(
            candidate.authority.grade
        )
        if target is None:
            return
        if target == Grade.REVIEW_MINUS:
            grants = meta_data.tables["skill_grants"]
            # No automatic restoration of a deliberately revoked authority.
            if (
                await self.session.scalar(
                    select(grants.c.id).where(grants.c.user_id == user_id, grants.c.competency_id == skill_id).limit(1)
                )
                is not None
            ):
                return
        try:
            gate = require_grade_entry(candidate.authority, await self.evidence.load(user_id, skill_id), target)
        except PolicyViolationError:
            return
        probation = start_probation(uuid4(), gate)
        grant_id = await self.replace(candidate, gate, now)
        await self.session.execute(
            insert(meta_data.tables["probations"]).values(
                id=probation.probation_id,
                created_at=now,
                user_id=user_id,
                competency_id=skill_id,
                grade=SkillGrade(target.value),
                skill_grant_id=grant_id,
                epoch=1,
                required_successes=3,
                version=1,
            )
        )

    async def replace(
        self,
        candidate: GradeCandidate,
        gate: GradeGateEvidence,
        now: datetime,
        *,
        assessment: dict[str, object] | None = None,
    ) -> UUID:
        """Preserve historical grade/evidence; revoke the old grant and append a new one."""
        if (gate.target_grade == Grade.EXPERT) != (assessment is not None):
            raise ValueError("Expert grade requires independent readiness evidence")
        skills, grants = meta_data.tables["user_competencies"], meta_data.tables["skill_grants"]
        if candidate.grant is not None:
            await self.session.execute(
                update(grants)
                .where(grants.c.id == candidate.grant.id)
                .values(
                    state=SkillGrantState.REVOKED,
                    revoked_at=now,
                    version=grants.c.version + 1,
                )
            )
        version = await self.session.scalar(
            update(skills)
            .where(skills.c.id == candidate.skill.id)
            .values(
                grade=SkillGrade(gate.target_grade.value),
                version=skills.c.version + 1,
            )
            .returning(skills.c.version)
        )
        fields: dict[str, object] = {}
        if assessment is not None:
            fields = {
                "readiness_assessment_id": assessment["id"],
                "confirmed_by_user_id": assessment["expert_user_id"],
                "confirmed_by_grant_id": assessment["expert_grant_id"],
                "confirmed_by_grant_version": assessment["expert_grant_version"],
                "confirmed_at": now,
            }
        grant_id = uuid4()
        await self.session.execute(
            insert(grants).values(
                id=grant_id,
                created_at=now,
                user_id=candidate.skill.user_id,
                competency_id=candidate.skill.competency_id,
                user_competency_version=version,
                grade=SkillGrade(gate.target_grade.value),
                state=SkillGrantState.CONFIRMED if assessment is not None else SkillGrantState.PROVISIONAL,
                evidence_kind=SkillGrantEvidenceKind.INDEPENDENT_READINESS
                if assessment is not None
                else SkillGrantEvidenceKind.PROGRESSION,
                evidence=encode({
                    **gate_record(gate),
                    "candidate_version": candidate.skill.version,
                    "previous_grant_id": candidate.grant.id if candidate.grant is not None else None,
                    "previous_grant_version": candidate.grant.version if candidate.grant is not None else None,
                }).decode(),
                version=1,
                readiness_decision=ReadinessDecision.CONFIRMED,
                confirmed_by_grade=SkillGrade.EXPERT,
                **fields,
            )
        )
        return grant_id
