from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.auth_identity.errors import ForbiddenError
from src.application.common.entity import Entity, to_entity
from src.application.user.entity import User, UserId

UserProfileId = NewType("UserProfileId", UUID)
UserCapabilityId = NewType("UserCapabilityId", UUID)


class ProductCapability(StrEnum):
    PARTICIPANT = "participant"
    ORGANIZER = "organizer"


@to_entity
class UserProfile(Entity[UserProfileId]):
    """Additive profile, unique by user_id; no authoritative global skill grade."""

    user_id: UserId
    display_name: str
    wants_to_review: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        if self.version < 1:
            raise ValueError("version must be positive")


@to_entity
class UserCapability(Entity[UserCapabilityId]):
    """Explicit product access, independent of legacy auth roles and skill grants."""

    user_id: UserId
    capability: ProductCapability
    revoked_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be positive")


def has_product_capability(user: User, capability: ProductCapability, grants: Iterable[UserCapability]) -> bool:
    return user.deleted_at is None and any(
        grant.user_id == user.id
        and grant.capability == capability
        and grant.deleted_at is None
        and grant.revoked_at is None
        for grant in grants
    )


def can_manage_owned_case(user: User, owner_id: UserId, grants: Iterable[UserCapability]) -> bool:
    """No admin bypass: both explicit organizer capability and ownership are required."""
    return user.id == owner_id and has_product_capability(user, ProductCapability.ORGANIZER, grants)


def require_owned_case_organizer(user: User, owner_id: UserId, grants: Iterable[UserCapability]) -> None:
    if not can_manage_owned_case(user, owner_id, grants):
        raise ForbiddenError
