"""Owner projections; no mutation, commits, job initialization or external I/O."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, and_, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CompoundSelect

from src.adapters.data_gateways.grade_evidence import SqlGradeEvidence
from src.adapters.data_gateways.review_matching import ReviewMatching
from src.adapters.data_gateways.tables import meta_data
from src.adapters.data_gateways.wallet_reader import DefaultWalletReader
from src.application.development.entity import (
    Probation,
    ReviewerApplicationState,
    SkillGrade,
    SkillGrantState,
    UserCompetency,
)
from src.application.development.policy_types import Grade, Grant, ProbationSnapshot
from src.application.profile.progression import next_grade
from src.application.profile.queries import (
    Activity,
    ExpertApplicationEligibility,
    GrantEvidence,
    Profile,
    ProfileCompetency,
    ProfileNotFoundError,
    ProfileWallet,
    SkillProbation,
)
from src.application.review.entity import ReviewState
from src.application.user.capability import ProductCapability
from src.application.user.entity import User, UserId
from src.application.wallet.queries import WalletNotFoundError


class SqlProfileReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _actor(self, actor: UserId) -> tuple[User, tuple[ProductCapability, ...]]:
        users, capabilities = meta_data.tables["users"], meta_data.tables["user_capabilities"]
        # FIRST row lock: SHARE conflicts with publication's owner NO KEY UPDATE
        # fence. Never acquire locks on another user, wallet, grant or assignment.
        # This prevents mixing pre-publication grades with post-publication money.
        user = await self.session.scalar(
            select(User)
            .where(users.c.id == actor, users.c.deleted_at.is_(None))
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise ProfileNotFoundError
        grants = tuple(
            await self.session.scalars(
                select(capabilities.c.capability)
                .where(
                    capabilities.c.user_id == actor,
                    capabilities.c.deleted_at.is_(None),
                    capabilities.c.revoked_at.is_(None),
                )
                .order_by(capabilities.c.capability)
            )
        )
        return user, grants

    async def profile(self, actor: UserId) -> Profile:
        with self.session.no_autoflush:
            return await self._profile(actor)

    async def _profile(self, actor: UserId) -> Profile:
        user, capabilities = await self._actor(actor)
        profiles, skills, definitions = (
            meta_data.tables[name] for name in ("user_profiles", "user_competencies", "competencies")
        )
        stored_profile = (
            (
                await self.session.execute(
                    select(profiles).where(profiles.c.user_id == actor, profiles.c.deleted_at.is_(None))
                )
            )
            .mappings()
            .one_or_none()
        )
        # Canonical live predicates include explicit expert provenance and the
        # exact provisional-grant/probation binding. Numeric values never grant rights.
        # Personal profile/history need authenticated ownership, not admission to
        # participant workflows. Never advertise reviewer authority after that
        # capability is revoked or for a newly registered, unprovisioned user.
        rights = (
            await ReviewMatching(self.session).rights(user) if ProductCapability.PARTICIPANT in capabilities else {}
        )
        records = (
            await self.session.execute(
                select(UserCompetency, definitions.c.name)
                .join(definitions, definitions.c.id == skills.c.competency_id)
                .where(skills.c.user_id == actor, skills.c.deleted_at.is_(None), definitions.c.deleted_at.is_(None))
                .order_by(skills.c.competency_id)
                .execution_options(populate_existing=True)
            )
        ).tuples()
        probations = await self._probations(actor)
        by_grant = {probation.grant_id: probation for probation in probations}
        competencies = []
        applications = meta_data.tables["reviewer_applications"]
        pending = dict(
            (
                await self.session.execute(
                    select(applications.c.competency_id, applications.c.id).where(
                        applications.c.user_id == actor,
                        applications.c.state == ReviewerApplicationState.PENDING,
                        applications.c.deleted_at.is_(None),
                        applications.c.requested_grade == SkillGrade.EXPERT,
                    )
                )
            )
            .tuples()
            .all()
        )
        for competency, name in records:
            right = rights.get(competency.competency_id)
            grant = right.grant if right is not None else None
            progress = (
                next_grade(
                    competency.grade,
                    competency.contribution_sum,
                    await SqlGradeEvidence(self.session).load(actor, competency.competency_id),
                )
                if competency.grade != SkillGrade.EXPERT
                else None
            )
            competencies.append(
                ProfileCompetency(
                    competency_id=competency.competency_id,
                    name=name,
                    contribution_sum=competency.contribution_sum,
                    value=competency.displayed_value,
                    grade=competency.grade,
                    version=competency.version,
                    grant=right.authority.grant if right is not None else Grant.NONE,
                    review_allowed=right is not None,
                    expert_confirmed=right.authority.expert_confirmed if right is not None else False,
                    evidence=GrantEvidence(
                        grant_id=grant.id,
                        grant_version=grant.version,
                        user_competency_version=grant.user_competency_version,
                        readiness_assessment_id=grant.readiness_assessment_id,
                        confirmed_by_grant_id=grant.confirmed_by_grant_id,
                        confirmed_by_grant_version=grant.confirmed_by_grant_version,
                    )
                    if grant is not None
                    else None,
                    probation=by_grant.get(grant.id) if grant is not None else None,
                    expert_application=ExpertApplicationEligibility(
                        can_apply=ProductCapability.PARTICIPANT in capabilities
                        and competency.grade == SkillGrade.REVIEW_PLUS
                        and competency.competency_id not in pending
                        and await SqlGradeEvidence(self.session).can_apply(user, competency.competency_id),
                        pending_application_id=pending.get(competency.competency_id),
                    ),
                    next_grade=progress,
                )
            )
        wallet = await DefaultWalletReader(self.session).get_wallet(actor)
        if wallet is None:
            # Never synthesize welcome money or initialize a wallet on a GET.
            raise WalletNotFoundError
        grades = tuple(SkillGrade)
        highest = max(
            (item.grade for item in competencies if item.review_allowed), key=grades.index, default=SkillGrade.PERFORMER
        )
        return Profile(
            id=user.id,
            # Legacy users may have no optional review-mode profile. Use the same
            # neutral label as review-mode creation, never an email/credential.
            display_name=stored_profile["display_name"] if stored_profile is not None else "Участник",
            role=user.role,
            capabilities=capabilities,
            grade=highest,
            competencies=tuple(competencies),
            probation=probations,
            wants_to_review=stored_profile["wants_to_review"] if stored_profile is not None else False,
            wallet=ProfileWallet(settled=wallet.settled, reserved=wallet.reserved, available=wallet.available),
            karma=wallet.karma,
            prodits=wallet.available,
        )

    async def _probations(self, actor: UserId) -> tuple[SkillProbation, ...]:
        table, grants, successes = (
            meta_data.tables[name] for name in ("probations", "skill_grants", "probation_successes")
        )
        rows = tuple(
            await self.session.scalars(
                select(Probation)
                .join(grants, and_(grants.c.id == table.c.skill_grant_id, grants.c.user_id == actor))
                .where(
                    table.c.user_id == actor,
                    table.c.deleted_at.is_(None),
                    grants.c.deleted_at.is_(None),
                    grants.c.revoked_at.is_(None),
                    grants.c.state.in_([SkillGrantState.PROVISIONAL, SkillGrantState.CONFIRMED]),
                )
                .order_by(table.c.competency_id, table.c.id)
                .execution_options(populate_existing=True)
            )
        )
        if not rows:
            return ()
        review_ids: dict[UUID, set[UUID]] = {row.id: set() for row in rows}
        evidence = await self.session.execute(
            select(successes.c.probation_id, successes.c.review_id).where(
                successes.c.probation_id.in_(review_ids),
                successes.c.reviewer_id == actor,
                successes.c.deleted_at.is_(None),
            )
        )
        for probation_id, review_id in evidence:
            review_ids[probation_id].add(review_id)
        result = []
        for row in rows:
            snapshot = ProbationSnapshot(
                probation_id=row.id,
                user_id=actor,
                skill_id=row.competency_id,
                grade=Grade(row.grade.value),
                confirmed_review_ids=frozenset(review_ids[row.id]),
            )
            result.append(
                SkillProbation(
                    id=row.id,
                    competency_id=row.competency_id,
                    grade=row.grade,
                    grant_id=row.skill_grant_id,
                    version=row.version,
                    epoch=row.epoch,
                    confirmed_reviews=len(snapshot.confirmed_review_ids),
                    remaining=3 - len(snapshot.confirmed_review_ids),
                    passed=snapshot.passed,
                    completed_at=row.completed_at,
                )
            )
        return tuple(result)

    async def history(self, actor: UserId, *, limit: int, offset: int) -> tuple[Activity, ...]:
        with self.session.no_autoflush:
            await self._actor(actor)
            if offset > 2**63 - 1:
                return ()
            statement = self._history(actor).subquery()
            rows = (
                await self.session.execute(
                    select(statement)
                    .order_by(statement.c.created_at.desc(), statement.c.type, statement.c.source_id)
                    .limit(limit)
                    .offset(offset)
                )
            ).mappings()
            return tuple(
                Activity(
                    id=f"{row['type']}:{row['source_id']}",
                    type=row["type"],
                    resource_id=row["resource_id"],
                    description=row["description"],
                    created_at=row["created_at"],
                )
                for row in rows
            )

    def _history(self, actor: UserId) -> "SelectHistory":
        projects, submissions, reviews, revisions, quality, ledger, probations, successes = (
            meta_data.tables[name]
            for name in (
                "projects",
                "submissions",
                "review_results",
                "review_revisions",
                "quality_assessments",
                "wallet_ledger",
                "probations",
                "probation_successes",
            )
        )
        applications, assessments = (
            meta_data.tables[name] for name in ("reviewer_applications", "reviewer_application_assessments")
        )
        return union_all(
            _event("case_started", projects.c.id, projects.c.id, "Проект начат", projects.c.created_at).where(
                projects.c.participant_id == actor, projects.c.deleted_at.is_(None)
            ),
            _event(
                "solution_submitted", submissions.c.id, submissions.c.id, "Решение отправлено", submissions.c.created_at
            )
            .join(projects, projects.c.id == submissions.c.project_id)
            .where(
                projects.c.participant_id == actor,
                projects.c.deleted_at.is_(None),
                submissions.c.deleted_at.is_(None),
                submissions.c.operation_id.is_not(None),
            ),
            # Completion revisions are PRIVATE reviewer activity, never author activity.
            _event(
                "review_completed",
                revisions.c.id,
                reviews.c.id,
                "Ревью сохранено для завершения",
                revisions.c.created_at,
            )
            .join(reviews, reviews.c.id == revisions.c.review_id)
            .where(reviews.c.reviewer_id == actor, reviews.c.deleted_at.is_(None), revisions.c.deleted_at.is_(None)),
            _event(
                "review_published", reviews.c.id, reviews.c.id, "Моё ревью опубликовано", reviews.c.published_at
            ).where(
                reviews.c.reviewer_id == actor, reviews.c.state == ReviewState.PUBLISHED, reviews.c.deleted_at.is_(None)
            ),
            _event(
                "feedback_published",
                reviews.c.id,
                reviews.c.id,
                "Получено опубликованное ревью",
                reviews.c.published_at,
            )
            .join(submissions, submissions.c.id == reviews.c.submission_id)
            .join(projects, projects.c.id == submissions.c.project_id)
            .where(
                projects.c.participant_id == actor,
                projects.c.deleted_at.is_(None),
                submissions.c.deleted_at.is_(None),
                reviews.c.state == ReviewState.PUBLISHED,
                reviews.c.deleted_at.is_(None),
            ),
            _event(
                "quality_assessed", quality.c.id, quality.c.id, "Оценено качество ревью", quality.c.created_at
            ).where(quality.c.assessor_id == actor, quality.c.deleted_at.is_(None)),
            # Financial postings are distinct from academic publication, not another reward.
            _event("wallet_transaction", ledger.c.id, ledger.c.id, "Проводка кошелька", ledger.c.created_at).where(
                ledger.c.owner_id == actor
            ),
            _event(
                "probation_started",
                probations.c.id,
                probations.c.id,
                "Начат испытательный срок",
                probations.c.created_at,
            ).where(probations.c.user_id == actor, probations.c.deleted_at.is_(None)),
            _event(
                "probation_completed",
                probations.c.id,
                probations.c.id,
                "Испытательный срок пройден",
                probations.c.completed_at,
            ).where(
                probations.c.user_id == actor, probations.c.deleted_at.is_(None), probations.c.completed_at.is_not(None)
            ),
            _event(
                "probation_progress",
                successes.c.id,
                successes.c.probation_id,
                "Засчитано подтверждённое ревью",
                successes.c.created_at,
            ).where(successes.c.reviewer_id == actor, successes.c.deleted_at.is_(None)),
            _event(
                "readiness_requested",
                applications.c.id,
                applications.c.id,
                "Подана заявка на допуск к ревью",
                applications.c.created_at,
            ).where(applications.c.user_id == actor, applications.c.deleted_at.is_(None)),
            _event(
                "readiness_assessed",
                assessments.c.id,
                assessments.c.application_id,
                "Оценена готовность к ревью",
                assessments.c.created_at,
            ).where(assessments.c.expert_user_id == actor, assessments.c.deleted_at.is_(None)),
            _event(
                "readiness_decided",
                assessments.c.id,
                assessments.c.application_id,
                "Получено решение по готовности к ревью",
                assessments.c.created_at,
            ).where(assessments.c.applicant_id == actor, assessments.c.deleted_at.is_(None)),
        )


type SelectHistory = CompoundSelect[tuple[str, UUID, UUID, str, datetime]]


def _event(
    kind: str,
    source: ColumnElement[UUID],
    resource: ColumnElement[UUID],
    description: str,
    created: ColumnElement[datetime],
) -> Select[tuple[str, UUID, UUID, str, datetime]]:
    return select(
        literal(kind).label("type"),
        source.label("source_id"),
        resource.label("resource_id"),
        literal(description).label("description"),
        created.label("created_at"),
    )
