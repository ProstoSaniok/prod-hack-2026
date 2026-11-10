from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from src.adapters.data_gateways.base_repository import BaseRepository
from src.adapters.data_gateways.tables import access_token_table, auth_identity_table, meta_data, user_table
from src.application.access_token.entity import AccessToken, AccessTokenId
from src.application.auth_identity.entity import AuthIdentity, AuthMethod
from src.application.auth_identity.errors import UserAlreadyExistsError
from src.application.user.capability import ProductCapability, UserCapability, UserCapabilityId
from src.application.user.entity import User, UserId, UserRole


class DefaultAuthRepository(BaseRepository):
    async def ensure_participant(self, user: User) -> None:
        """Provision ordinary accounts once, preserving every historical access decision."""
        # Fence concurrent successful logins before checking the permanent grant history.
        current = await self._session.scalar(
            select(User)
            .where(user_table.c.id == user.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if current is None or current.deleted_at is not None or current.role != UserRole.USER:
            return
        table = meta_data.tables["user_capabilities"]
        historical = await self._session.scalar(select(table.c.id).where(table.c.user_id == user.id).limit(1))
        if historical is None:
            self._session.add(
                UserCapability(
                    id=UserCapabilityId(uuid4()),
                    created_at=datetime.now(UTC),
                    user_id=user.id,
                    capability=ProductCapability.PARTICIPANT,
                )
            )
            await self._session.flush()

    async def find_capabilities(self, user_id: UserId) -> list[ProductCapability]:
        connection = await self._session.connection()
        if not await connection.run_sync(
            lambda sync: inspect(sync).has_table(
                "user_capabilities", schema=sync.get_execution_options().get("schema_translate_map", {}).get(None)
            )
        ):
            return []
        table = meta_data.tables["user_capabilities"]
        result = await self._session.scalars(
            select(table.c.capability)
            .where(
                table.c.user_id == user_id,
                table.c.revoked_at.is_(None),
                table.c.deleted_at.is_(None),
            )
            .order_by(table.c.capability)
        )
        return list(result)

    async def find_identity(self, email: str) -> AuthIdentity | None:
        statement = select(AuthIdentity).where(
            auth_identity_table.c.identifier == email,
            auth_identity_table.c.method == AuthMethod.EMAIL,
            auth_identity_table.c.deleted_at.is_(None),
        )
        lookup = self._find_one_or_none(statement)
        return await lookup

    async def find_user(self, user_id: UserId) -> User | None:
        statement = select(User).where(user_table.c.id == user_id, user_table.c.deleted_at.is_(None))
        lookup = self._find_one_or_none(statement)
        return await lookup

    async def find_token(self, token_id: AccessTokenId) -> AccessToken | None:
        statement = select(AccessToken).where(access_token_table.c.id == token_id)
        lookup = self._find_one_or_none(statement)
        return await lookup

    async def register(self, user: User, identity: AuthIdentity, token: AccessToken) -> None:
        try:
            self._add(user)
            await self._flush()
            self._add_all([identity, token])
            await self._flush()
        except IntegrityError as error:
            await self._session.rollback()
            if await self.find_identity(identity.identifier) is not None:
                raise UserAlreadyExistsError from error
            raise
