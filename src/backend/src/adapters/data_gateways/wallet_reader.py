from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.tables import meta_data
from src.application.user.entity import UserId
from src.application.wallet.entity import BusinessSource, LedgerEntry, LedgerEntryId, ReservationId, SourceKind, Wallet
from src.application.wallet.queries import WalletUnavailableError


def _field[T](row: RowMapping, name: str, kind: type[T]) -> T:
    value: object = row[name]
    if not isinstance(value, kind):
        raise WalletUnavailableError
    return value


def _optional_uuid(row: RowMapping, name: str) -> UUID | None:
    return None if row[name] is None else _field(row, name, UUID)


def _entry(row: RowMapping) -> LedgerEntry:
    scope = _optional_uuid(row, "source_scope_id")
    related = _optional_uuid(row, "related_entry_id")
    reservation = _optional_uuid(row, "reservation_id")
    return LedgerEntry(
        id=LedgerEntryId(_field(row, "id", UUID)),
        owner_id=UserId(_field(row, "owner_id", UUID)),
        source=BusinessSource(
            kind=_field(row, "source_kind", SourceKind),
            subject_id=_field(row, "source_subject_id", UUID),
            scope_id=UserId(scope) if scope is not None else None,
        ),
        created_at=_field(row, "created_at", datetime),
        sequence=_field(row, "sequence", int),
        settled_delta=_field(row, "settled_delta", int),
        reserved_delta=_field(row, "reserved_delta", int),
        karma_delta=_field(row, "karma_delta", int),
        related_entry_id=LedgerEntryId(related) if related is not None else None,
        reservation_id=ReservationId(reservation) if reservation is not None else None,
        job_id=_optional_uuid(row, "job_id"),
        assignment_id=_optional_uuid(row, "assignment_id"),
        case_id=_optional_uuid(row, "case_id"),
        origin_revision_id=_optional_uuid(row, "origin_revision_id"),
    )


class DefaultWalletReader:
    """Owner-scoped reads: no creation, locks, autoflush or financial effects."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_wallet(self, owner_id: UserId) -> Wallet | None:
        table = meta_data.tables["wallets"]
        try:
            with self.session.no_autoflush:
                row = (
                    (await self.session.execute(select(table).where(table.c.owner_id == owner_id)))
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError:
            raise WalletUnavailableError from None
        if row is None:
            return None
        return Wallet(
            owner_id=owner_id,
            settled=_field(row, "settled", int),
            reserved=_field(row, "reserved", int),
            karma=_field(row, "karma", int),
            version=_field(row, "version", int),
        )

    async def list_entries(self, owner_id: UserId, *, limit: int, offset: int) -> list[LedgerEntry]:
        table = meta_data.tables["wallet_ledger"]
        statement = (
            select(table)
            .where(table.c.owner_id == owner_id)
            .order_by(table.c.sequence.desc())
            .limit(limit)
            .offset(offset)
        )
        try:
            with self.session.no_autoflush:
                rows = (await self.session.execute(statement)).mappings().all()
        except SQLAlchemyError:
            raise WalletUnavailableError from None
        return [_entry(row) for row in rows]
