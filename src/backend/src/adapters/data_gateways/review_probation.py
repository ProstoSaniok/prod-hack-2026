"""Exact original-assignment probation effects in the publication caller's UoW."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.learning_views import row_mapping
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_records import value
from src.application.catalog.management import CaseConflictError
from src.application.development.entity import ReadinessDecision, SkillGrade, SkillGrantState
from src.application.development.policy import advance_probation
from src.application.development.policy_types import (
    Grade,
    Grant,
    ProbationBinding,
    ProbationSnapshot,
    QualityDecision,
    QualityEvidence,
    ReviewSnapshot,
    SkillAuthorityInput,
)
from src.application.review.entity import QualityDecision as PersistedQualityDecision
from src.application.review.finalization import PublicationConfirmation, PublicationSkill, ReviewPublication


class SqlReviewProbationEffects:
    """Never map a historical review to a replacement grant or probation epoch.

    The parent locks all participating users before assignment/review/rights locks.
    That fence also serializes these shared-to-exclusive skill/grant lock upgrades.
    This collaborator stages effects only; it never commits or acquires wallets.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def advance(self, publication: ReviewPublication) -> None:
        confirmation = publication.confirmation
        if not publication.supervision_required or confirmation is None:
            raise CaseConflictError("SUPERVISION_REQUIRED", "Probation requires confirmed supervised publication")
        bindings = await self._bindings(publication)
        review = ReviewSnapshot(
            review_id=publication.review_id,
            revision_id=publication.revision_id,
            author_id=publication.author_id,
            reviewer_id=publication.reviewer_id,
            skill_ids=frozenset(skill.competency_id for skill in publication.skills),
            probation_bindings=tuple(
                ProbationBinding(
                    probation_id=value(binding, "probation_id", UUID),
                    skill_id=value(binding, "competency_id", UUID),
                    grade=Grade(value(binding, "grade", SkillGrade).value),
                )
                for binding in bindings
                if binding["probation_id"] is not None
            ),
        )
        evidence = await self._evidence(publication, confirmation, review)
        experts = {skill.competency_id: skill for skill in confirmation.skills}
        # _bindings sorts by skill UUID, independently of caller input ordering.
        for binding in bindings:
            if binding["probation_id"] is not None:
                await self._advance_binding(
                    publication, binding, evidence, experts[value(binding, "competency_id", UUID)]
                )

    async def _bindings(self, publication: ReviewPublication) -> tuple[dict[str, object], ...]:
        bindings, assignments, reviews, grants = (
            meta_data.tables[name]
            for name in ("assignment_competencies", "review_assignments", "review_results", "skill_grants")
        )
        rows = (
            (
                await self.session.execute(
                    select(
                        bindings,
                        assignments.c.supervised.label("assignment_supervised"),
                        grants.c.user_id.label("original_grantee"),
                        grants.c.competency_id.label("original_skill"),
                        grants.c.grade.label("original_grade"),
                        grants.c.version.label("original_grant_version"),
                    )
                    .join(assignments, assignments.c.id == bindings.c.assignment_id)
                    .join(reviews, reviews.c.assignment_id == assignments.c.id)
                    .join(grants, grants.c.id == bindings.c.grant_id)
                    .where(
                        reviews.c.id == publication.review_id,
                        reviews.c.reviewer_id == publication.reviewer_id,
                        reviews.c.submission_id == publication.submission_id,
                    )
                    .order_by(bindings.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        skills = {skill.competency_id: skill for skill in publication.skills}
        if (
            not rows
            or {row["competency_id"] for row in rows} != skills.keys()
            or not any(row["probation_id"] is not None for row in rows)
            or any(
                not row["assignment_supervised"]
                or row["deleted_at"] is not None
                or row["grant_state"] not in {SkillGrantState.PROVISIONAL, SkillGrantState.CONFIRMED}
                or (row["grant_state"] == SkillGrantState.PROVISIONAL) != (row["probation_id"] is not None)
                # A correction snapshots current rights, which can be a later
                # grade. Its supervision remains bound to the original grant.
                or row["original_grantee"] != publication.reviewer_id
                or row["original_skill"] != row["competency_id"]
                or row["original_grade"] != row["grade"]
                or row["original_grant_version"] < row["grant_version"]
                or tuple(SkillGrade).index(skills[row["competency_id"]].reviewer_grade)
                < tuple(SkillGrade).index(row["grade"])
                for row in rows
            )
        ):
            raise CaseConflictError("PROBATION_CONFLICT", "Original assignment supervision evidence is inconsistent")
        return tuple(row_mapping(row) for row in rows)

    async def _evidence(
        self, publication: ReviewPublication, confirmation: PublicationConfirmation, review: ReviewSnapshot
    ) -> QualityEvidence:
        assessments, snapshots = meta_data.tables["quality_assessments"], meta_data.tables["assessor_snapshots"]
        assessment = (
            (
                await self.session.execute(
                    select(assessments).where(
                        assessments.c.id == confirmation.assessment_id,
                        assessments.c.revision_id == publication.revision_id,
                        assessments.c.submission_id == publication.submission_id,
                        assessments.c.assessor_id == confirmation.expert_id,
                        assessments.c.quality_score == confirmation.quality_score,
                        assessments.c.decision == PersistedQualityDecision.CONFIRMED,
                        assessments.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        rows = (
            (
                await self.session.execute(
                    select(snapshots)
                    .where(snapshots.c.assessment_id == confirmation.assessment_id)
                    .order_by(snapshots.c.competency_id)
                )
            )
            .mappings()
            .all()
        )
        experts = {skill.competency_id: skill for skill in confirmation.skills}
        if (
            assessment is None
            or confirmation.expert_id in {publication.author_id, publication.reviewer_id}
            or len(experts) != len(confirmation.skills)
            or experts.keys() != review.skill_ids
            or {row["competency_id"] for row in rows} != review.skill_ids
            or any(
                row["deleted_at"] is not None
                or row["grade"] != SkillGrade.EXPERT
                or row["grant_state"] != SkillGrantState.CONFIRMED
                or row["grant_id"] != experts[row["competency_id"]].grant_id
                or row["grant_version"] != experts[row["competency_id"]].grant_version
                or row["user_competency_version"] != experts[row["competency_id"]].competency_version
                or row["value"] != experts[row["competency_id"]].reviewer_value
                for row in rows
            )
        ):
            raise CaseConflictError(
                "SUPERVISION_REQUIRED", "Persisted confirmation must match this revision and all skills"
            )
        return QualityEvidence(
            review=review,
            assignment_id=value(assessment, "assignment_id", UUID),
            expert_id=confirmation.expert_id,
            expert_snapshots=tuple(
                SkillAuthorityInput(
                    user_id=confirmation.expert_id,
                    skill_id=value(row, "competency_id", UUID),
                    value=value(row, "value", Decimal),
                    grade=Grade.EXPERT,
                    grant=Grant.CONFIRMED,
                    expert_confirmed=True,
                )
                for row in rows
            ),
            decision=QualityDecision.CONFIRMED,
            quality_score=value(assessment, "quality_score", Decimal),
            feedback=value(assessment, "feedback", str),
        )

    async def _advance_binding(
        self,
        publication: ReviewPublication,
        binding: dict[str, object],
        evidence: QualityEvidence,
        expert: PublicationSkill,
    ) -> None:
        confirmation = publication.confirmation
        if confirmation is None:
            raise CaseConflictError("SUPERVISION_REQUIRED", "Probation requires the persisted quality assessment")
        probations, successes = meta_data.tables["probations"], meta_data.tables["probation_successes"]
        probation = (
            (
                await self.session.execute(
                    select(probations).where(probations.c.id == binding["probation_id"]).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            probation is None
            or probation["user_id"] != publication.reviewer_id
            or probation["competency_id"] != binding["competency_id"]
            or probation["grade"] != binding["grade"]
            or probation["skill_grant_id"] != binding["grant_id"]
            or probation["required_successes"] != 3
            or probation["epoch"] < 1
            or probation["deleted_at"] is not None
        ):
            raise CaseConflictError("PROBATION_CONFLICT", "Original probation identity is unavailable")
        if probation["completed_at"] is not None:
            return
        grant, competency = await self._live_grant(publication, binding)
        review_ids = frozenset(
            await self.session.scalars(select(successes.c.review_id).where(successes.c.probation_id == probation["id"]))
        )
        before = ProbationSnapshot(
            probation_id=value(probation, "id", UUID),
            user_id=publication.reviewer_id,
            skill_id=value(binding, "competency_id", UUID),
            grade=Grade(value(binding, "grade", SkillGrade).value),
            confirmed_review_ids=review_ids,
        )
        after = advance_probation(before, evidence.review, evidence)
        if after == before:
            return
        await self.session.execute(
            insert(successes).values(
                id=uuid4(),
                created_at=publication.created_at,
                probation_id=probation["id"],
                reviewer_id=publication.reviewer_id,
                competency_id=binding["competency_id"],
                review_id=publication.review_id,
                review_revision_id=publication.revision_id,
                quality_assessment_id=confirmation.assessment_id,
                expert_user_id=evidence.expert_id,
                expert_grant_id=expert.grant_id,
                expert_grant_version=expert.grant_version,
                quality_score=evidence.quality_score,
                quality_decision=ReadinessDecision.CONFIRMED,
            )
        )
        await self.session.execute(
            update(probations)
            .where(probations.c.id == probation["id"], probations.c.version == probation["version"])
            .values(
                version=probations.c.version + 1,
                completed_at=publication.created_at if after.passed else None,
            )
        )
        if after.passed:
            grants, competencies = meta_data.tables["skill_grants"], meta_data.tables["user_competencies"]
            # Rights change but the per-skill grade/value do not. Bump both live
            # versions and bind the grant to the new aggregate version atomically.
            competency_version = await self.session.scalar(
                update(competencies)
                .where(competencies.c.id == competency["id"], competencies.c.version == competency["version"])
                .values(version=competencies.c.version + 1)
                .returning(competencies.c.version)
            )
            await self.session.execute(
                update(grants)
                .where(grants.c.id == grant["id"], grants.c.version == grant["version"])
                .values(
                    state=SkillGrantState.CONFIRMED,
                    version=grants.c.version + 1,
                    user_competency_version=competency_version,
                    confirmed_at=publication.created_at,
                    confirmed_by_user_id=evidence.expert_id,
                    confirmed_by_grant_id=expert.grant_id,
                    confirmed_by_grant_version=expert.grant_version,
                )
            )

    async def _live_grant(
        self, publication: ReviewPublication, binding: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, object]]:
        grants, competencies = meta_data.tables["skill_grants"], meta_data.tables["user_competencies"]
        competency = (
            (
                await self.session.execute(
                    select(competencies)
                    .where(
                        competencies.c.user_id == publication.reviewer_id,
                        competencies.c.competency_id == binding["competency_id"],
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        grant = (
            (await self.session.execute(select(grants).where(grants.c.id == binding["grant_id"]).with_for_update()))
            .mappings()
            .one_or_none()
        )
        if (
            grant is None
            or competency is None
            or grant["user_id"] != publication.reviewer_id
            or grant["competency_id"] != binding["competency_id"]
            or grant["grade"] != binding["grade"]
            or competency["grade"] != binding["grade"]
            or grant["state"] != SkillGrantState.PROVISIONAL
            or grant["deleted_at"] is not None
            or grant["revoked_at"] is not None
            or competency["deleted_at"] is not None
            or grant["version"] < binding["grant_version"]
            or grant["user_competency_version"] > competency["version"]
        ):
            raise CaseConflictError("PROBATION_CONFLICT", "Original provisional grant is no longer current")
        return row_mapping(grant), row_mapping(competency)
