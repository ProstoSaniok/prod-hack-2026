from src.application.auth_identity.errors import ForbiddenError
from src.application.user.entity import User, UserId, UserRole


def require_role(user: User, *roles: UserRole) -> None:
    if user.role not in roles:
        raise ForbiddenError


def require_owner(user: User, owner_id: UserId) -> None:
    if user.id != owner_id and user.role != UserRole.ADMIN:
        raise ForbiddenError
