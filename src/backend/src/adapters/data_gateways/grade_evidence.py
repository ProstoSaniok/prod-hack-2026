"""Bounded, durable evidence for the canonical development gates."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.catalog.management import CaseNotFoundError
from src.application.development.entity import (
    SkillGrade,
    SkillGrant,
    SkillGrantState,
    UserCompetency,
    has_expert_rights,
    has_review_rights,
)
from src.application.development.policy import advance_probation, request_expert_readiness
from src.application.development.policy_types import (
    Grade,
    GradeEvidenceInput,
    GradeGateEvidence,
    Grant,
    PolicyViolationError,
    ProbationBinding,
    ProbationSnapshot,
    QualityDecision,
    QualityEvidence,
    ReviewSnapshot,
    SkillAuthorityInput,
    SuccessfulCaseInput,
)
from src.application.review.entity import QualityDecision as StoredQualityDecision, ReviewState
from src.application.user.entity import User


def exact_value(total: Decimal) -> Decimal:
    # Comparison/min never consult Decimal's ambient arithmetic precision. In
    # particular 49.999... must not acquire the displayed value's 50 threshold.
    return min(total, Decimal(100))


def gate_record(gate: GradeGateEvidence) -> dict[str, object]:
    return {
        **asdict(gate),
        "successful_case_ids": sorted(map(str, gate.successful_case_ids)),
        "good_review_ids": sorted(map(str, gate.good_review_ids)),
        "passed_probation_ids": sorted(map(str, gate.passed_probation_ids)),
    }


@dataclass(frozen=True)
class GradeCandidate:
    skill: UserCompetency
    grant: SkillGrant | None
    authority: SkillAuthorityInput


class SqlGradeEvidence:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def expert_skills(self, user: User) -> frozenset[UUID]:
        skills, grants = meta_data.tables["user_competencies"], meta_data.tables["skill_grants"]
        rows = (
            await self.session.execute(
                select(UserCompetency, SkillGrant)
                .join(
                    SkillGrant,
                    (grants.c.user_id == skills.c.user_id) & (grants.c.competency_id == skills.c.competency_id),
                )
                .where(
                    skills.c.user_id == user.id,
                    skills.c.deleted_at.is_(None),
                    grants.c.deleted_at.is_(None),
                    grants.c.state == SkillGrantState.CONFIRMED,
                    grants.c.revoked_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).tuples()
        return frozenset(
            skill.competency_id
            for skill, grant in rows
            if exact_value(skill.contribution_sum) >= 75 and has_expert_rights(user, skill.competency_id, skill, grant)
        )

    async def can_apply(self, user: User, skill_id: UUID) -> bool:
        try:
            candidate = await self.candidate(user, skill_id, lock=False)
            request_expert_readiness(skill_id, candidate.authority, await self.load(user.id, skill_id))
        except (CaseNotFoundError, PolicyViolationError):
            return False
        return True

    async def candidate(self, user: User, skill_id: UUID, *, lock: bool = True) -> GradeCandidate:
        skills, grants = meta_data.tables["user_competencies"], meta_data.tables["skill_grants"]
        skill_query = (
            select(UserCompetency)
            .where(skills.c.user_id == user.id, skills.c.competency_id == skill_id, skills.c.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        skill = await self.session.scalar(skill_query.with_for_update() if lock else skill_query)
        if skill is None:
            raise CaseNotFoundError
        grant_query = (
            select(SkillGrant)
            .where(
                grants.c.user_id == user.id,
                grants.c.competency_id == skill_id,
                grants.c.deleted_at.is_(None),
                grants.c.state != SkillGrantState.REVOKED,
            )
            .execution_options(populate_existing=True)
        )
        grant = await self.session.scalar(grant_query.with_for_update() if lock else grant_query)
        confirmed = grant is not None and has_review_rights(user, skill_id, skill, grant)
        return GradeCandidate(
            skill,
            grant,
            SkillAuthorityInput(
                user_id=user.id,
                skill_id=skill_id,
                value=exact_value(skill.contribution_sum),
                grade=Grade(skill.grade.value),
                grant=Grant.CONFIRMED if confirmed else Grant.NONE,
                expert_confirmed=grant is not None and has_expert_rights(user, skill_id, skill, grant),
            ),
        )

    async def load(self, user_id: UUID, skill_id: UUID) -> GradeEvidenceInput:
        contributions, reviews = meta_data.tables["progression_contributions"], meta_data.tables["review_results"]
        cases = (
            (
                await self.session.execute(
                    select(contributions.c.case_id, contributions.c.score)
                    .join(reviews, reviews.c.id == contributions.c.review_id)
                    .where(
                        contributions.c.participant_id == user_id,
                        contributions.c.competency_id == skill_id,
                        contributions.c.effective.is_(True),
                        contributions.c.deleted_at.is_(None),
                        contributions.c.score >= 70,
                        reviews.c.state == ReviewState.PUBLISHED,
                        reviews.c.published_revision_id == contributions.c.review_revision_id,
                        reviews.c.deleted_at.is_(None),
                    )
                    .order_by(contributions.c.case_id)
                    .limit(2)
                )
            )
            .mappings()
            .all()
        )
        table, successes = meta_data.tables["probations"], meta_data.tables["probation_successes"]
        probations: list[ProbationSnapshot] = []
        quality: list[QualityEvidence] = []
        for grade in (SkillGrade.REVIEW_MINUS, SkillGrade.REVIEW_PLUS):
            probation = (
                (
                    await self.session.execute(
                        select(table)
                        .where(
                            table.c.user_id == user_id,
                            table.c.competency_id == skill_id,
                            table.c.grade == grade,
                            table.c.completed_at.is_not(None),
                            table.c.deleted_at.is_(None),
                        )
                        .order_by(table.c.epoch.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if probation is None:
                continue
            before = ProbationSnapshot(
                probation_id=probation["id"], user_id=user_id, skill_id=skill_id, grade=Grade(grade.value)
            )
            rows = (
                (
                    await self.session.execute(
                        select(successes)
                        .where(successes.c.probation_id == probation["id"], successes.c.deleted_at.is_(None))
                        .order_by(successes.c.review_id)
                        .limit(3)
                    )
                )
                .mappings()
                .all()
            )
            for success in rows:
                evidence = await self._quality(success["quality_assessment_id"])
                if evidence is not None:
                    before = advance_probation(before, evidence.review, evidence)
                    quality.append(evidence)
            if before.passed:
                probations.append(before)
        return GradeEvidenceInput(
            successful_cases=tuple(
                SuccessfulCaseInput(
                    participant_id=user_id,
                    case_id=row["case_id"],
                    skill_id=skill_id,
                    solution_score=row["score"],
                    approved=True,
                    finalized=True,
                )
                for row in cases
            ),
            quality_assessments=tuple(quality),
            probations=tuple(probations),
        )

    async def _quality(self, assessment_id: UUID) -> QualityEvidence | None:
        assessments, reviews, submissions = (
            meta_data.tables[name] for name in ("quality_assessments", "review_results", "submissions")
        )
        row = (
            (
                await self.session.execute(
                    select(
                        assessments,
                        reviews.c.id.label("review_id"),
                        reviews.c.reviewer_id,
                        reviews.c.assignment_id.label("original_assignment_id"),
                        submissions.c.author_id,
                    )
                    .join(reviews, reviews.c.published_revision_id == assessments.c.revision_id)
                    .join(submissions, submissions.c.id == reviews.c.submission_id)
                    .where(
                        assessments.c.id == assessment_id,
                        assessments.c.deleted_at.is_(None),
                        assessments.c.decision == StoredQualityDecision.CONFIRMED,
                        reviews.c.state == ReviewState.PUBLISHED,
                        reviews.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        snapshots, bindings = meta_data.tables["assessor_snapshots"], meta_data.tables["assignment_competencies"]
        experts = (
            (
                await self.session.execute(
                    select(snapshots)
                    .where(snapshots.c.assessment_id == assessment_id, snapshots.c.deleted_at.is_(None))
                    .order_by(snapshots.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        original = (
            (
                await self.session.execute(
                    select(bindings)
                    .where(bindings.c.assignment_id == row["original_assignment_id"], bindings.c.deleted_at.is_(None))
                    .order_by(bindings.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        if not original or {item["competency_id"] for item in experts} != {item["competency_id"] for item in original}:
            return None
        return QualityEvidence(
            review=ReviewSnapshot(
                review_id=row["review_id"],
                revision_id=row["revision_id"],
                author_id=row["author_id"],
                reviewer_id=row["reviewer_id"],
                skill_ids=frozenset(item["competency_id"] for item in original),
                probation_bindings=tuple(
                    ProbationBinding(
                        probation_id=item["probation_id"],
                        skill_id=item["competency_id"],
                        grade=Grade(item["grade"].value),
                    )
                    for item in original
                    if item["probation_id"] is not None
                ),
            ),
            assignment_id=row["assignment_id"],
            expert_id=row["assessor_id"],
            expert_snapshots=tuple(
                SkillAuthorityInput(
                    user_id=row["assessor_id"],
                    skill_id=item["competency_id"],
                    value=item["value"],
                    grade=Grade(item["grade"].value),
                    grant=Grant.CONFIRMED if item["grant_state"] == SkillGrantState.CONFIRMED else Grant.NONE,
                    expert_confirmed=item["grade"] == SkillGrade.EXPERT,
                )
                for item in experts
            ),
            decision=QualityDecision.CONFIRMED,
            quality_score=row["quality_score"],
            feedback=row["feedback"],
        )
