from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.application.auth_identity.errors import ForbiddenError
from src.application.development import (
    SkillGrade,
    SkillGrant,
    SkillGrantEvidenceKind,
    SkillGrantId,
    SkillGrantState,
    UserCompetency,
    UserCompetencyId,
    has_expert_rights,
    has_review_rights,
)
from src.application.user.capability import (
    ProductCapability,
    UserCapability,
    UserCapabilityId,
    UserProfile,
    UserProfileId,
    can_manage_owned_case,
    has_product_capability,
    require_owned_case_organizer,
)
from src.application.user.entity import User, UserId, UserRole


@pytest.fixture
def organizer() -> User:
    return User.factory()


@pytest.fixture
def capability(organizer: User) -> UserCapability:
    return UserCapability(
        id=UserCapabilityId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=organizer.id,
        capability=ProductCapability.ORGANIZER,
    )


def test_organizer_can_manage_only_owned_cases(organizer: User, capability: UserCapability) -> None:
    assert can_manage_owned_case(organizer, organizer.id, [capability])
    require_owned_case_organizer(organizer, organizer.id, [capability])
    other_owner = UserId(uuid4())
    assert not can_manage_owned_case(organizer, other_owner, [capability])
    with pytest.raises(ForbiddenError):
        require_owned_case_organizer(organizer, other_owner, [capability])


def test_legacy_admin_neither_grants_capability_nor_bypasses_ownership(
    organizer: User, capability: UserCapability
) -> None:
    organizer.role = UserRole.ADMIN
    assert not has_product_capability(organizer, ProductCapability.ORGANIZER, [])
    with pytest.raises(ForbiddenError):
        require_owned_case_organizer(organizer, organizer.id, [])
    with pytest.raises(ForbiddenError):
        require_owned_case_organizer(organizer, UserId(uuid4()), [capability])
    assert can_manage_owned_case(organizer, organizer.id, [capability])


def test_participant_and_other_users_capabilities_do_not_authorize_case_management(
    organizer: User, capability: UserCapability
) -> None:
    participant = replace(capability, capability=ProductCapability.PARTICIPANT)
    assert has_product_capability(organizer, ProductCapability.PARTICIPANT, [participant])
    assert not can_manage_owned_case(organizer, organizer.id, [participant])
    someone_elses = replace(capability, user_id=UserId(uuid4()))
    assert not can_manage_owned_case(organizer, organizer.id, [someone_elses])
    with pytest.raises(ValueError, match="version"):
        replace(capability, version=0)


@pytest.mark.parametrize("record", ["user", "capability", "revoked"])
def test_capability_checks_use_live_records(organizer: User, capability: UserCapability, record: str) -> None:
    assert can_manage_owned_case(organizer, organizer.id, [capability])
    now = datetime.now(tz=UTC)
    if record == "user":
        organizer.deleted_at = now
    elif record == "capability":
        capability.deleted_at = now
    else:
        capability.revoked_at = now
    assert not has_product_capability(organizer, ProductCapability.ORGANIZER, [capability])
    with pytest.raises(ForbiddenError):
        require_owned_case_organizer(organizer, organizer.id, [capability])


def test_organizer_capability_conveys_no_academic_rights(organizer: User, capability: UserCapability) -> None:
    skill_id = uuid4()
    competency = UserCompetency(
        id=UserCompetencyId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=organizer.id,
        competency_id=skill_id,
    )
    provisional = SkillGrant(
        id=SkillGrantId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=organizer.id,
        competency_id=skill_id,
        user_competency_version=1,
        grade=SkillGrade.EXPERT,
        state=SkillGrantState.PROVISIONAL,
        evidence_kind=SkillGrantEvidenceKind.PROGRESSION,
        evidence="Numeric evidence does not establish expert readiness",
    )
    assert has_product_capability(organizer, ProductCapability.ORGANIZER, [capability])
    assert not has_review_rights(organizer, skill_id, competency, provisional, supervised=True)
    assert not has_expert_rights(organizer, skill_id, competency, provisional)


def test_profile_is_additive_and_review_preference_grants_no_capability(organizer: User) -> None:
    profile = UserProfile(
        id=UserProfileId(uuid4()),
        created_at=datetime.now(tz=UTC),
        user_id=organizer.id,
        display_name="Участник PROD.Guild",
        wants_to_review=True,
    )
    assert organizer.role == UserRole.USER
    assert profile.user_id == organizer.id
    assert profile.wants_to_review
    assert not has_product_capability(organizer, ProductCapability.ORGANIZER, [])
    with pytest.raises(ValueError, match="display_name"):
        replace(profile, display_name=" ")
    with pytest.raises(ValueError, match="version"):
        replace(profile, version=0)
