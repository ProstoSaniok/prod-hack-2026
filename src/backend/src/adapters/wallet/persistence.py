"""Wallet Core serializer on the caller's AsyncSession and outer transaction.

Requires T02's wallet metadata/migration handoff. There is no fallback schema,
implicit transaction, commit, rollback, savepoint or retry in this collaborator.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from psycopg import Error as PostgreSQLError
from sqlalchemy import RowMapping, Table, and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from src.adapters.data_gateways.tables import meta_data
from src.application.user.entity import UserId
from src.application.wallet.entity import (
    BusinessSource,
    LedgerEntry,
    LedgerEntryId,
    Reservation,
    ReservationId,
    ReservationState,
    SourceKind,
    Wallet,
    WalletChange,
)
from src.application.wallet.errors import WalletInvariantError, WalletWriteConflictError

BIGINT_MAX = 2**63 - 1
BIGINT_MIN = -(2**63)
_DELTAS = {
    SourceKind.WELCOME: (10, 0, 0),
    SourceKind.SUCCESSFUL_CASE: (3, 0, 0),
    SourceKind.REVIEW_REWARD: (5, 0, 1),
    SourceKind.RESERVE: (0, 5, 0),
    SourceKind.SETTLE: (-5, -5, 0),
    SourceKind.RELEASE: (0, -5, 0),
    SourceKind.REFUND: (5, 0, 0),
}
_RACE_CONSTRAINTS = frozenset({
    "wallet_ledger_pkey",
    "wallet_reservations_pkey",
    "uq_wallet_source_unscoped",
    "uq_wallet_source_scoped",
    "uq_wallet_ledger_sequence",
    "uq_wallet_active_job",
    "uq_wallet_reserve_entry",
    "uq_wallet_settlement_entry",
    "uq_wallet_terminal_entry",
})


def _field[T](row: RowMapping, key: str, kind: type[T]) -> T:
    value: object = row[key]
    if not isinstance(value, kind):
        raise WalletInvariantError("Invalid wallet persistence record")
    return value


def _optional[T](row: RowMapping, key: str, kind: type[T]) -> T | None:
    return None if row[key] is None else _field(row, key, kind)


def _wallet(row: RowMapping) -> Wallet:
    return Wallet(
        owner_id=UserId(_field(row, "owner_id", UUID)),
        settled=_field(row, "settled", int),
        reserved=_field(row, "reserved", int),
        karma=_field(row, "karma", int),
        version=_field(row, "version", int),
    )


def _reservation(row: RowMapping) -> Reservation:
    settlement = _optional(row, "settlement_entry_id", UUID)
    terminal = _optional(row, "terminal_entry_id", UUID)
    return Reservation(
        id=ReservationId(_field(row, "id", UUID)),
        owner_id=UserId(_field(row, "owner_id", UUID)),
        job_id=_field(row, "job_id", UUID),
        state=_field(row, "state", ReservationState),
        reserve_entry_id=LedgerEntryId(_field(row, "reserve_entry_id", UUID)),
        created_at=_field(row, "created_at", datetime),
        assignment_id=_optional(row, "assignment_id", UUID),
        settlement_entry_id=LedgerEntryId(settlement) if settlement is not None else None,
        terminal_entry_id=LedgerEntryId(terminal) if terminal is not None else None,
    )


def _entry(row: RowMapping) -> LedgerEntry:
    scope = _optional(row, "source_scope_id", UUID)
    related = _optional(row, "related_entry_id", UUID)
    reservation = _optional(row, "reservation_id", UUID)
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
        job_id=_optional(row, "job_id", UUID),
        assignment_id=_optional(row, "assignment_id", UUID),
        case_id=_optional(row, "case_id", UUID),
        origin_revision_id=_optional(row, "origin_revision_id", UUID),
    )


def _wallet_values(wallet: Wallet) -> dict[str, object]:
    return {
        "owner_id": wallet.owner_id,
        "settled": wallet.settled,
        "reserved": wallet.reserved,
        "karma": wallet.karma,
        "version": wallet.version,
    }


def _reservation_values(hold: Reservation) -> dict[str, object]:
    return {
        "id": hold.id,
        "owner_id": hold.owner_id,
        "job_id": hold.job_id,
        "state": hold.state.name,
        "reserve_entry_id": hold.reserve_entry_id,
        "created_at": hold.created_at,
        "assignment_id": hold.assignment_id,
        "settlement_entry_id": hold.settlement_entry_id,
        "terminal_entry_id": hold.terminal_entry_id,
    }


def _entry_values(entry: LedgerEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "owner_id": entry.owner_id,
        "source_kind": entry.source.kind.name,
        "source_subject_id": entry.source.subject_id,
        "source_scope_id": entry.source.scope_id,
        "created_at": entry.created_at,
        "sequence": entry.sequence,
        "settled_delta": entry.settled_delta,
        "reserved_delta": entry.reserved_delta,
        "karma_delta": entry.karma_delta,
        "related_entry_id": entry.related_entry_id,
        "reservation_id": entry.reservation_id,
        "job_id": entry.job_id,
        "assignment_id": entry.assignment_id,
        "case_id": entry.case_id,
        "origin_revision_id": entry.origin_revision_id,
    }


def _check_change(change: WalletChange) -> None:
    before, after, entry = change.before, change.after, change.entry
    integers = (
        before.settled,
        before.reserved,
        before.karma,
        before.version,
        after.settled,
        after.reserved,
        after.karma,
        after.version,
        entry.sequence,
        entry.settled_delta,
        entry.reserved_delta,
        entry.karma_delta,
    )
    if any(type(value) is not int or not BIGINT_MIN <= value <= BIGINT_MAX for value in integers):
        raise WalletInvariantError("Wallet integer exceeds signed BIGINT storage range")
    if (
        before.owner_id != after.owner_id
        or entry.owner_id != before.owner_id
        or entry.sequence != before.version + 1
        or after
        != replace(
            before,
            settled=before.settled + entry.settled_delta,
            reserved=before.reserved + entry.reserved_delta,
            karma=before.karma + entry.karma_delta,
            version=entry.sequence,
        )
        or (entry.settled_delta, entry.reserved_delta, entry.karma_delta) != _DELTAS[entry.source.kind]
    ):
        raise WalletInvariantError("Wallet change does not reconcile its snapshot, sequence and source")
    _check_hold_change(change)


def _check_hold_change(change: WalletChange) -> None:
    entry, before, after = change.entry, change.reservation_before, change.reservation_after
    kind = entry.source.kind
    if kind in {SourceKind.WELCOME, SourceKind.SUCCESSFUL_CASE, SourceKind.REVIEW_REWARD}:
        if before is not None or after is not None:
            raise WalletInvariantError("Reward cannot mutate a reservation")
        return
    if (
        after is None
        or after.owner_id != entry.owner_id
        or after.id != entry.reservation_id
        or after.id != entry.source.subject_id
        or after.job_id != entry.job_id
    ):
        raise WalletInvariantError("Reservation change has inconsistent identity")
    if kind == SourceKind.RESERVE:
        if (
            before is not None
            or after.state != ReservationState.HELD
            or after.reserve_entry_id != entry.id
            or after.created_at != entry.created_at
        ):
            raise WalletInvariantError("Reserve requires a new backed hold")
        return
    if before is None:
        raise WalletInvariantError("Reservation transition requires its previous state")
    if kind == SourceKind.SETTLE:
        expected = replace(
            before, state=ReservationState.SETTLED, assignment_id=entry.assignment_id, settlement_entry_id=entry.id
        )
        valid = before.state == ReservationState.HELD and entry.related_entry_id == before.reserve_entry_id
    elif kind == SourceKind.RELEASE:
        expected = replace(before, state=ReservationState.RELEASED, terminal_entry_id=entry.id)
        valid = before.state == ReservationState.HELD and entry.related_entry_id == before.reserve_entry_id
    else:
        expected = replace(before, state=ReservationState.REFUNDED, terminal_entry_id=entry.id)
        valid = (
            before.state == ReservationState.SETTLED
            and entry.related_entry_id == before.settlement_entry_id
            and entry.assignment_id == before.assignment_id
        )
    if not valid or after != expected:
        raise WalletInvariantError("Invalid reservation transition or changed historical provenance")


class SqlAlchemyWalletTransaction:
    """One instance per caller's outer transaction, never a shared/session factory.

    Caller prelocks operation/request/business sources, domain and ordered skill
    rows. This adapter acquires all owner guards before wallet rows, in UUID order.
    No global source lock is acquired late. SQL and CAS errors require outer rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._guards = meta_data.tables["wallet_owner_guards"]
        self._wallets = meta_data.tables["wallets"]
        self._ledger = meta_data.tables["wallet_ledger"]
        self._reservations = meta_data.tables["wallet_reservations"]
        self._transaction: AsyncSessionTransaction | None = None
        self._owners: frozenset[UserId] = frozenset()
        self._failed = False

    def _require_transaction(self, owner_id: UserId | None = None) -> None:
        if (
            self._failed
            or self._transaction is None
            or self._session.get_transaction() is not self._transaction
            or not self._transaction.is_active
            or self._session.in_nested_transaction()
            or (owner_id is not None and owner_id not in self._owners)
        ):
            raise WalletInvariantError("Wallet requires its original active UoW and prelocked owners")

    @contextmanager
    def _storage(self) -> Iterator[None]:
        try:
            with self._session.no_autoflush:
                yield
        except DBAPIError as error:
            self._failed = True
            origin = error.orig
            if isinstance(origin, PostgreSQLError):
                if origin.sqlstate in {"40001", "40P01"} or (
                    origin.sqlstate == "23505" and origin.diag.constraint_name in _RACE_CONSTRAINTS
                ):
                    raise WalletWriteConflictError("Wallet write conflict; roll back the entire UoW") from None
                if origin.sqlstate in {"22003", "23503", "23514", "23502", "55000"}:
                    raise WalletInvariantError("Wallet persistence constraint rejected the change") from None
            raise WalletInvariantError("Wallet storage failed; roll back the entire UoW") from None
        except BaseException:
            self._failed = True
            raise

    async def lock_wallets(self, owner_ids: tuple[UserId, ...]) -> None:
        if (
            self._transaction is not None
            or not owner_ids
            or tuple(sorted(set(owner_ids), key=lambda x: x.int)) != owner_ids
        ):
            raise WalletInvariantError("Acquire all wallet owners once in sorted UUID order")
        self._transaction = self._session.get_transaction()
        self._require_transaction()
        with self._storage():
            for owner in owner_ids:
                await self._session.execute(
                    pg_insert(self._guards).values(owner_id=owner).on_conflict_do_nothing(index_elements=["owner_id"])
                )
                await self._session.execute(
                    select(self._guards).where(self._guards.c.owner_id == owner).with_for_update()
                )
            for owner in owner_ids:
                await self._session.execute(
                    pg_insert(self._wallets)
                    .values(**_wallet_values(Wallet(owner_id=owner)))
                    .on_conflict_do_nothing(index_elements=["owner_id"])
                )
                await self._session.execute(
                    select(self._wallets).where(self._wallets.c.owner_id == owner).with_for_update()
                )
        self._owners = frozenset(owner_ids)

    async def get_wallet(self, owner_id: UserId) -> Wallet:
        self._require_transaction(owner_id)
        with self._storage():
            row = (
                (await self._session.execute(select(self._wallets).where(self._wallets.c.owner_id == owner_id)))
                .mappings()
                .one()
            )
        return _wallet(row)

    async def reconcile(self, owner_id: UserId) -> Wallet:
        """Locked audit of durable counters/history/held backing, without repairing data."""
        wallet = await self.get_wallet(owner_id)
        ledger = self._ledger
        with self._storage():
            row = (
                (
                    await self._session.execute(
                        select(
                            func.coalesce(func.sum(ledger.c.settled_delta), 0).label("settled"),
                            func.coalesce(func.sum(ledger.c.reserved_delta), 0).label("reserved"),
                            func.coalesce(func.sum(ledger.c.karma_delta), 0).label("karma"),
                            func.coalesce(func.max(ledger.c.sequence), 0).label("version"),
                            func.count().label("entry_count"),
                        ).where(ledger.c.owner_id == owner_id)
                    )
                )
                .mappings()
                .one()
            )
            held = await self._session.scalar(
                select(func.count())
                .select_from(self._reservations)
                .where(
                    self._reservations.c.owner_id == owner_id,
                    self._reservations.c.state == ReservationState.HELD.name,
                )
            )
        if (
            (wallet.settled, wallet.reserved, wallet.karma, wallet.version)
            != (row["settled"], row["reserved"], row["karma"], row["version"])
            or row["entry_count"] != wallet.version
            or held is None
            or wallet.reserved != 5 * held
        ):
            raise WalletInvariantError("Wallet history, snapshot or reservation backing does not reconcile")
        return wallet

    async def find_entry(self, source: BusinessSource) -> LedgerEntry | None:
        self._require_transaction()
        with self._storage():
            row = (
                (
                    await self._session.execute(
                        select(self._ledger).where(
                            self._ledger.c.source_kind == source.kind.name,
                            self._ledger.c.source_subject_id == source.subject_id,
                            self._ledger.c.source_scope_id.is_not_distinct_from(source.scope_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _entry(row) if row is not None else None

    async def get_reservation(self, owner_id: UserId, reservation_id: ReservationId) -> Reservation | None:
        self._require_transaction(owner_id)
        with self._storage():
            row = (
                (
                    await self._session.execute(
                        select(self._reservations).where(
                            self._reservations.c.owner_id == owner_id,
                            self._reservations.c.id == reservation_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _reservation(row) if row is not None else None

    async def active_reservation(self, job_id: UUID) -> Reservation | None:
        self._require_transaction()
        with self._storage():
            row = (
                (
                    await self._session.execute(
                        select(self._reservations).where(
                            self._reservations.c.job_id == job_id,
                            self._reservations.c.state.in_([ReservationState.HELD.name, ReservationState.SETTLED.name]),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _reservation(row) if row is not None else None

    async def _check_provenance(self, entry: LedgerEntry) -> None:
        """Resolve stable FK facts; current authority/supervision is the caller's duty."""
        tables = meta_data.tables
        projects, submissions, jobs = tables["projects"], tables["submissions"], tables["review_jobs"]
        if entry.source.kind in {SourceKind.SUCCESSFUL_CASE, SourceKind.REVIEW_REWARD}:
            revisions, reviews = tables["review_revisions"], tables["review_results"]
            statement = (
                select(reviews.c.id, reviews.c.reviewer_id, projects.c.participant_id, projects.c.case_id)
                .select_from(
                    revisions.join(reviews, revisions.c.review_id == reviews.c.id)
                    .join(submissions, reviews.c.submission_id == submissions.c.id)
                    .join(projects, submissions.c.project_id == projects.c.id)
                )
                .where(revisions.c.id == entry.origin_revision_id)
            )
            row = (await self._session.execute(statement)).mappings().one_or_none()
            beneficiary = "participant_id" if entry.source.kind == SourceKind.SUCCESSFUL_CASE else "reviewer_id"
            if row is None or row[beneficiary] != entry.owner_id or row["case_id"] != entry.case_id:
                raise WalletInvariantError("Reward provenance does not belong to this beneficiary and case")
            if entry.source.kind == SourceKind.REVIEW_REWARD and row["id"] != entry.source.subject_id:
                raise WalletInvariantError("Reward source does not identify the underlying review")
        if entry.reservation_id is not None:
            statement = (
                select(projects.c.participant_id)
                .select_from(
                    jobs.join(submissions, jobs.c.submission_id == submissions.c.id).join(
                        projects, submissions.c.project_id == projects.c.id
                    )
                )
                .where(jobs.c.id == entry.job_id, jobs.c.kind == "REVIEW")
            )
            if await self._session.scalar(statement) != entry.owner_id:
                raise WalletInvariantError("Priority job does not belong to this wallet")
        if entry.related_entry_id is not None:
            row = (
                (await self._session.execute(select(self._ledger).where(self._ledger.c.id == entry.related_entry_id)))
                .mappings()
                .one_or_none()
            )
            related = _entry(row) if row is not None else None
            expected = SourceKind.SETTLE if entry.source.kind == SourceKind.REFUND else SourceKind.RESERVE
            if (
                related is None
                or related.source.kind != expected
                or (entry.source.kind == SourceKind.REFUND and related.assignment_id != entry.assignment_id)
            ):
                raise WalletInvariantError("Priority change has the wrong predecessor entry")

    async def _cas(self, table: Table, before: Mapping[str, object], after: Mapping[str, object], key: str) -> None:
        statement = update(table).where(
            and_(*(table.c[name].is_not_distinct_from(value) for name, value in before.items()))
        )
        result = await self._session.execute(statement.values(**after).returning(table.c[key]))
        if result.scalar_one_or_none() is None:
            raise WalletWriteConflictError("Wallet snapshot or reservation CAS failed; roll back the entire UoW")

    async def _check_deferred_cycle(self) -> None:
        connection = await self._session.connection()
        synchronous = connection.sync_connection
        if synchronous is None:
            raise WalletInvariantError("Wallet connection is not active")
        schema = synchronous.schema_for_object(self._ledger)
        identifier = "fk_wallet_ledger_reservation"
        if schema is not None:
            identifier = connection.dialect.identifier_preparer.quote_schema(schema) + "." + identifier
        # Identifiers come only from trusted metadata/schema translation, never a request.
        await self._session.execute(text("SET CONSTRAINTS " + identifier + " IMMEDIATE"))
        await self._session.execute(text("SET CONSTRAINTS " + identifier + " DEFERRED"))

    async def apply(self, change: WalletChange) -> None:
        self._require_transaction(change.before.owner_id)
        _check_change(change)
        with self._storage():
            await self._check_provenance(change.entry)
            await self._session.execute(insert(self._ledger).values(**_entry_values(change.entry)))
            await self._cas(self._wallets, _wallet_values(change.before), _wallet_values(change.after), "owner_id")
            if change.reservation_after is not None:
                after = _reservation_values(change.reservation_after)
                if change.reservation_before is None:
                    await self._session.execute(insert(self._reservations).values(**after))
                else:
                    await self._cas(self._reservations, _reservation_values(change.reservation_before), after, "id")
            # Check only our cycle. Do not force unrelated caller-owned deferred FKs.
            await self._check_deferred_cycle()
