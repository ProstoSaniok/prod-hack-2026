from datetime import timedelta
from uuid import uuid4

import pytest

from src.application.access_token.entity import AccessToken
from src.application.auth_identity.errors import ForbiddenError
from src.application.common.authorization import require_owner, require_role
from src.application.user.entity import User, UserId, UserRole


def test_owner_and_admin_access() -> None:
    user = User.factory()
    require_owner(user, user.id)
    with pytest.raises(ForbiddenError):
        require_owner(user, UserId(uuid4()))
    with pytest.raises(ForbiddenError):
        require_role(user, UserRole.ADMIN)
    user.role = UserRole.ADMIN
    require_owner(user, UserId(uuid4()))
    require_role(user, UserRole.ADMIN)


def test_token_expiration_and_revocation() -> None:
    user = User.factory()
    token = AccessToken.factory(user.id, timedelta(days=1))
    assert not token.expired_predicate()
    token.revoke()
    assert token.expired_predicate()
    assert AccessToken.factory(user.id, timedelta(seconds=-1)).expired_predicate()
