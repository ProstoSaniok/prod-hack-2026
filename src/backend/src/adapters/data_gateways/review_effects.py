"""Publication effects on the finalizer's session; no commit or external I/O."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.grade_advancement import SqlGradeAdvancement
from src.adapters.data_gateways.review_probation import SqlReviewProbationEffects
from src.adapters.data_gateways.tables import meta_data
from src.adapters.operations.sql_work import SqlOutbox
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.catalog.management import CaseConflictError
from src.application.development.entity import SkillGrade, SkillGrantState
from src.application.development.progression import calculate_solution_contribution
from src.application.development.progression_types import (
    PROGRESSION_POLICY_VERSION,
    AssessorSnapshot,
    QualityConfirmationSnapshot,
    SolutionProgressionInput,
)
from src.application.operations.entity import OutboxMessage, ProjectionSpec
from src.application.operations.work import Work
from src.application.review.entity import ReviewVerdict
from src.application.review.finalization import PublicationSkill, ReviewPublication, require_final_content
from src.application.wallet.policy import PublicationEvidence, QualityConfirmation
from src.application.wallet.service import WalletCollaborator


class SqlReviewPublicationEffects:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply(self, publication: ReviewPublication) -> None:
        require_final_content(publication.content)
        assessors = require_publication_assessors(publication)
        approved = publication.content.verdict == ReviewVerdict.APPROVED
        if approved:
            for skill in sorted(publication.skills, key=lambda item: item.competency_id.int):
                await self._progress(publication, skill, assessors[skill.competency_id])
        if publication.supervision_required:
            await SqlReviewProbationEffects(self.session).advance(publication)
        advancement = SqlGradeAdvancement(self.session)
        for user_id in sorted({publication.author_id, publication.reviewer_id}, key=lambda item: item.int):
            if user_id == publication.author_id and not approved:
                continue
            if user_id == publication.reviewer_id and not publication.supervision_required:
                continue
            for skill in sorted(publication.skills, key=lambda item: item.competency_id.int):
                await advancement.advance(user_id, skill.competency_id, publication.created_at)
        wallets = await WalletCollaborator.acquire(
            SqlAlchemyWalletTransaction(self.session), owner_ids=(publication.author_id, publication.reviewer_id)
        )
        evidence = PublicationEvidence(
            review_id=publication.review_id,
            revision_id=publication.revision_id,
            case_id=publication.case_id,
            author_id=publication.author_id,
            reviewer_id=publication.reviewer_id,
            case_skill_ids=frozenset(skill.competency_id for skill in publication.skills),
            published=True,
            valid_publication=True,
            approved=approved,
            score=publication.content.score,
            quality_required=publication.supervision_required,
            confirmation=(
                QualityConfirmation(
                    revision_id=publication.revision_id,
                    expert_id=publication.confirmation.expert_id,
                    confirmed_skill_ids=frozenset(assessors),
                    quality_score=publication.confirmation.quality_score,
                )
                if publication.confirmation is not None
                else None
            ),
        )
        if approved:
            await wallets.reward_case(evidence, now=publication.created_at)
        await wallets.reward_review(evidence, now=publication.created_at)
        # Durable publication event: consumers may project only this committed
        # immutable revision. A queued event is not a successful Git projection.
        await SqlOutbox(self.session, meta_data.tables["outbox_messages"]).enqueue(
            OutboxMessage(
                id=uuid4(),
                spec=ProjectionSpec(
                    topic="review.published",
                    source_key=str(publication.revision_id),
                    subject_id=publication.review_id,
                    revision=publication.revision_number,
                ),
                work=Work(created_at=publication.created_at, available_at=publication.created_at),
            )
        )

    async def _progress(
        self, publication: ReviewPublication, skill: PublicationSkill, assessor: PublicationSkill
    ) -> None:
        confirmation = publication.confirmation
        source = SolutionProgressionInput(
            source_id=publication.revision_id,
            source_version=publication.revision_number,
            expected_source_version=publication.revision_number,
            participant_id=publication.author_id,
            case_id=publication.case_id,
            case_version=publication.case_version,
            expected_case_version=publication.case_version,
            policy_version=PROGRESSION_POLICY_VERSION,
            skill_id=skill.competency_id,
            solution_score=publication.content.score,
            weight=skill.weight,
            verdict="approved",
            finalized=True,
            supervision_required=publication.supervision_required,
            reviewer=AssessorSnapshot(
                assessor_id=publication.reviewer_id,
                skill_id=skill.competency_id,
                version=skill.competency_version,
                expertise=skill.reviewer_value,
                confirmed_expert=skill.reviewer_grade == SkillGrade.EXPERT,
            ),
            confirmation=(
                QualityConfirmationSnapshot(
                    assessment_id=confirmation.assessment_id,
                    version=1,
                    expected_version=1,
                    source_id=publication.revision_id,
                    source_version=publication.revision_number,
                    case_version=publication.case_version,
                    policy_version=PROGRESSION_POLICY_VERSION,
                    decision="confirmed",
                    quality_score=confirmation.quality_score,
                    expert=AssessorSnapshot(
                        assessor_id=confirmation.expert_id,
                        skill_id=assessor.competency_id,
                        version=assessor.competency_version,
                        expertise=assessor.reviewer_value,
                        confirmed_expert=True,
                    ),
                )
                if confirmation is not None
                else None
            ),
        )
        contribution = calculate_solution_contribution(source)
        if contribution is None:
            raise ValueError("Approved publication did not produce a valid contribution")
        skills, contributions = meta_data.tables["user_competencies"], meta_data.tables["progression_contributions"]
        before = (
            (
                await self.session.execute(
                    select(skills)
                    .where(skills.c.user_id == publication.author_id, skills.c.competency_id == skill.competency_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if before["deleted_at"] is not None:
            raise CaseConflictError("SKILL_UNAVAILABLE", "Author skill is unavailable")
        existing = await self.session.scalar(
            select(contributions.c.id).where(
                contributions.c.participant_id == publication.author_id,
                contributions.c.case_id == publication.case_id,
                contributions.c.competency_id == skill.competency_id,
                contributions.c.effective.is_(True),
            )
        )
        if existing is not None:
            raise CaseConflictError("RESULT_CONFLICT", "This case already has an effective skill contribution")
        # PostgreSQL NUMERIC preserves the exact policy contribution and imported
        # baseline. Positive NUMERIC round(..., 2) implements half-up display.
        total = skills.c.contribution_sum + contribution.amount
        after = (
            (
                await self.session.execute(
                    update(skills)
                    .where(skills.c.id == before["id"])
                    .values(
                        contribution_sum=total,
                        displayed_value=func.round(func.least(100, total), 2),
                        version=skills.c.version + 1,
                    )
                    .returning(skills)
                )
            )
            .mappings()
            .one()
        )
        await self.session.execute(
            insert(contributions).values(
                id=uuid4(),
                created_at=publication.created_at,
                participant_id=publication.author_id,
                case_id=publication.case_id,
                competency_id=skill.competency_id,
                review_id=publication.review_id,
                review_revision_id=publication.revision_id,
                source_id=publication.revision_id,
                policy_version=PROGRESSION_POLICY_VERSION,
                case_version=publication.case_version,
                score=publication.content.score,
                weight=skill.weight,
                assessor_id=contribution.assessor.assessor_id,
                assessor_grant_id=assessor.grant_id,
                assessor_grant_version=assessor.grant_version,
                assessor_competency_version=contribution.assessor.version,
                expertise_value=contribution.assessor.expertise,
                contribution=contribution.amount,
                contribution_sum_before=before["contribution_sum"],
                contribution_sum_after=after["contribution_sum"],
                displayed_value_before=before["displayed_value"],
                displayed_value_after=after["displayed_value"],
                effective=True,
                version=1,
            )
        )


def require_publication_assessors(publication: ReviewPublication) -> dict[UUID, PublicationSkill]:
    """Validate trusted snapshot coverage; the caller still owns current-rights locks."""
    skills = {skill.competency_id: skill for skill in publication.skills}
    if (
        not skills
        or len(skills) != len(publication.skills)
        or publication.author_id == publication.reviewer_id
        or type(publication.revision_number) is not int
        or publication.revision_number < 1
    ):
        raise CaseConflictError("PUBLICATION_INVALID", "Publication requires unique skills and a valid revision")
    if not publication.supervision_required:
        if publication.confirmation is not None or any(
            skill.grant_state != SkillGrantState.CONFIRMED for skill in publication.skills
        ):
            raise CaseConflictError("SUPERVISION_REQUIRED", "Current confirmed reviewer rights are required")
        return skills
    confirmation = publication.confirmation
    if (
        confirmation is None
        or confirmation.expert_id in {publication.author_id, publication.reviewer_id}
        or not valid_quality_score(confirmation.quality_score, minimum=70)
        or any(
            skill.grant_state not in {SkillGrantState.PROVISIONAL, SkillGrantState.CONFIRMED}
            for skill in publication.skills
        )
    ):
        raise CaseConflictError("SUPERVISION_REQUIRED", "Independent expert confirmation is required")
    experts = {skill.competency_id: skill for skill in confirmation.skills}
    if (
        experts.keys() != skills.keys()
        or len(experts) != len(confirmation.skills)
        or any(
            skill.grant_state != SkillGrantState.CONFIRMED
            or skill.reviewer_grade != SkillGrade.EXPERT
            or not valid_quality_score(skill.reviewer_value, minimum=75)
            or type(skill.grant_version) is not int
            or skill.grant_version < 1
            or type(skill.competency_version) is not int
            or skill.competency_version < 1
            for skill in confirmation.skills
        )
    ):
        raise CaseConflictError("SUPERVISION_REQUIRED", "Confirmed expert coverage of every case skill is required")
    return experts


def valid_quality_score(score: object, *, minimum: int) -> bool:
    return isinstance(score, Decimal) and score.is_finite() and minimum <= score <= 100
