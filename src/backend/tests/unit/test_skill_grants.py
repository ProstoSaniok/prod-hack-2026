from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.application.development import (
    ReviewerApplicationAssessmentId,
    SkillGrade,
    SkillGrant,
    SkillGrantEvidenceKind,
    SkillGrantId,
    SkillGrantState,
    SkillSnapshot,
    UserCompetency,
    UserCompetencyId,
    has_expert_rights,
    has_review_rights,
)
from src.application.user.entity import User, UserId


@pytest.fixture
def user() -> User:
    return User.factory()


@pytest.fixture
def sql_skill_id() -> UUID:
    return uuid4()


@pytest.fixture
def competency(user: User, sql_skill_id: UUID) -> UserCompetency:
    return UserCompetency(
        id=UserCompetencyId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=user.id,
        competency_id=sql_skill_id,
        contribution_sum=Decimal("112.123456789"),
        displayed_value=Decimal(100),
        grade=SkillGrade.EXPERT,
    )


@pytest.fixture
def grant(user: User, sql_skill_id: UUID) -> SkillGrant:
    return SkillGrant(
        id=SkillGrantId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=user.id,
        competency_id=sql_skill_id,
        user_competency_version=1,
        grade=SkillGrade.EXPERT,
        state=SkillGrantState.CONFIRMED,
        evidence_kind=SkillGrantEvidenceKind.INDEPENDENT_READINESS,
        evidence="Independent SQL readiness assessment after reviewer gates",
        readiness_assessment_id=ReviewerApplicationAssessmentId(uuid4()),
        confirmed_by_user_id=UserId(uuid4()),
        confirmed_by_grant_id=SkillGrantId(uuid4()),
        confirmed_by_grant_version=2,
        confirmed_at=datetime.now(tz=UTC),
    )


def test_sql_expertise_does_not_authorize_unrelated_skill(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant
) -> None:
    assert has_expert_rights(user, sql_skill_id, competency, grant)
    api_skill_id = uuid4()
    assert not has_expert_rights(user, api_skill_id, competency, grant)
    api_competency = replace(competency, competency_id=api_skill_id, grade=SkillGrade.PERFORMER)
    assert not has_review_rights(user, api_skill_id, api_competency, grant)
    assert not has_expert_rights(User.factory(), sql_skill_id, competency, grant)


@pytest.mark.parametrize("supervised", [True])
def test_provisional_expert_is_never_authorized(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant, *, supervised: bool
) -> None:
    provisional = replace(grant, state=SkillGrantState.PROVISIONAL, confirmed_at=None)
    assert not has_review_rights(user, sql_skill_id, competency, provisional, supervised=supervised)
    assert not has_expert_rights(user, sql_skill_id, competency, provisional)


@pytest.mark.parametrize("grade", [SkillGrade.REVIEW_MINUS])
def test_provisional_reviewer_needs_explicit_supervision(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant, grade: SkillGrade
) -> None:
    current = replace(competency, grade=grade)
    provisional = replace(grant, grade=grade, state=SkillGrantState.PROVISIONAL, confirmed_at=None)
    assert not has_review_rights(user, sql_skill_id, current, provisional)
    assert has_review_rights(user, sql_skill_id, current, provisional, supervised=True)
    assert not has_expert_rights(user, sql_skill_id, current, provisional)


def test_revocation_is_live_not_overridden_by_historical_snapshot(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant
) -> None:
    snapshot = SkillSnapshot(
        user_id=user.id,
        competency_id=sql_skill_id,
        user_competency_version=competency.version,
        value=competency.displayed_value,
        grade=competency.grade,
        grant_id=grant.id,
        grant_version=grant.version,
        grant_state=grant.state,
    )
    assert has_expert_rights(user, sql_skill_id, competency, grant)
    grant.revoked_at = datetime.now(tz=UTC)
    assert not has_expert_rights(user, sql_skill_id, competency, grant)
    grant.state = SkillGrantState.REVOKED
    grant.version += 1
    assert not has_review_rights(user, sql_skill_id, competency, grant, supervised=True)
    assert snapshot.grant_state == SkillGrantState.CONFIRMED
    assert snapshot.grant_version == 1
    field_name = "value"
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, field_name, Decimal(0))


@pytest.mark.parametrize("record", ["user", "competency", "grant"])
def test_deleted_records_never_authorize(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant, record: str
) -> None:
    records: dict[str, User | UserCompetency | SkillGrant] = {
        "user": user,
        "competency": competency,
        "grant": grant,
    }
    records[record].deleted_at = datetime.now(tz=UTC)
    assert not has_review_rights(user, sql_skill_id, competency, grant, supervised=True)


def test_performer_and_mismatched_current_grade_never_authorize(
    user: User, sql_skill_id: UUID, competency: UserCompetency, grant: SkillGrant
) -> None:
    competency.grade = SkillGrade.PERFORMER
    assert not has_review_rights(user, sql_skill_id, competency, grant)
    with pytest.raises(ValueError, match="Performer"):
        replace(grant, grade=SkillGrade.PERFORMER)


def test_numeric_evidence_alone_cannot_confirm_expert(grant: SkillGrant) -> None:
    with pytest.raises(ValueError, match="independent readiness or explicit seed"):
        replace(grant, evidence_kind=SkillGrantEvidenceKind.PROGRESSION)
    with pytest.raises(ValueError, match="own readiness"):
        replace(grant, confirmed_by_user_id=grant.user_id)
    with pytest.raises(ValueError, match="independent readiness or explicit seed"):
        replace(grant, confirmed_by_grant_id=None)
    with pytest.raises(ValueError, match="independent readiness or explicit seed"):
        replace(grant, confirmed_by_grant_id=grant.id)
    with pytest.raises(ValueError, match="independent readiness or explicit seed"):
        replace(grant, readiness_assessment_id=None)
