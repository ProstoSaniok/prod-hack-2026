"""Transactional TEST DOUBLE only: no SQL locks, migration or restart proof."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

from src.application.user.entity import UserId
from src.application.wallet.entity import (
    BusinessSource,
    LedgerEntry,
    Reservation,
    ReservationId,
    ReservationState,
    Wallet,
    WalletChange,
)
from src.application.wallet.errors import WalletInvariantError, WalletWriteConflictError

ACTIVE_STATES = {ReservationState.HELD, ReservationState.SETTLED}


@dataclass
class MemoryDatabase:
    wallets: dict[UserId, Wallet] = field(default_factory=dict)
    entries: dict[BusinessSource, LedgerEntry] = field(default_factory=dict)
    reservations: dict[ReservationId, Reservation] = field(default_factory=dict)
    locks: dict[UserId, asyncio.Lock] = field(default_factory=dict)
    commits: int = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["MemoryTransaction"]:
        transaction = MemoryTransaction(database=self)
        try:
            yield transaction
            transaction.commit()
        finally:
            transaction.close()


@dataclass
class MemoryTransaction:
    database: MemoryDatabase
    wallets: dict[UserId, Wallet] = field(default_factory=dict)
    originals: dict[UserId, Wallet] = field(default_factory=dict)
    entries: dict[BusinessSource, LedgerEntry] = field(default_factory=dict)
    reservations: dict[ReservationId, Reservation] = field(default_factory=dict)
    acquired: list[asyncio.Lock] = field(default_factory=list)
    lock_order: tuple[UserId, ...] = ()

    async def lock_wallets(self, owner_ids: tuple[UserId, ...]) -> None:
        if self.acquired or tuple(sorted(set(owner_ids), key=lambda owner: owner.int)) != owner_ids:
            raise WalletInvariantError("Acquire all wallets once in deterministic order")
        self.lock_order = owner_ids
        for owner in owner_ids:
            lock = self.database.locks.setdefault(owner, asyncio.Lock())
            await lock.acquire()
            self.acquired.append(lock)
            wallet = self.database.wallets.get(owner, Wallet(owner_id=owner))
            self.wallets[owner] = wallet
            self.originals[owner] = wallet

    async def get_wallet(self, owner_id: UserId) -> Wallet:
        await asyncio.sleep(0)
        return self.wallets[owner_id]

    async def find_entry(self, source: BusinessSource) -> LedgerEntry | None:
        await asyncio.sleep(0)
        return self.entries.get(source, self.database.entries.get(source))

    async def get_reservation(self, owner_id: UserId, reservation_id: ReservationId) -> Reservation | None:
        await asyncio.sleep(0)
        hold = self.reservations.get(reservation_id, self.database.reservations.get(reservation_id))
        return hold if hold is not None and hold.owner_id == owner_id else None

    async def active_reservation(self, job_id: UUID) -> Reservation | None:
        await asyncio.sleep(0)
        holds = self.database.reservations | self.reservations
        return next((hold for hold in holds.values() if hold.job_id == job_id and hold.state in ACTIVE_STATES), None)

    async def apply(self, change: WalletChange) -> None:
        entry = change.entry
        if self.wallets[change.before.owner_id] != change.before or await self.find_entry(entry.source) is not None:
            raise WalletWriteConflictError("Snapshot/source conflict")
        if change.reservation_after is not None:
            hold = change.reservation_after
            previous = await self.get_reservation(hold.owner_id, hold.id)
            active = await self.active_reservation(hold.job_id)
            if previous != change.reservation_before or (active is not None and active.id != hold.id):
                raise WalletWriteConflictError("Hold CAS/active job conflict")
            self.reservations[hold.id] = hold
        self.wallets[change.after.owner_id] = change.after
        self.entries[entry.source] = entry

    def commit(self) -> None:
        # No awaits: emulate an atomic commit, NOT a PostgreSQL implementation.
        if any(source in self.database.entries for source in self.entries):
            raise WalletWriteConflictError("Global permanent source conflict")
        for owner, original in self.originals.items():
            if self.database.wallets.get(owner, Wallet(owner_id=owner)) != original:
                raise WalletWriteConflictError("Stale snapshot")
        holds = self.database.reservations | self.reservations
        active_jobs = [hold.job_id for hold in holds.values() if hold.state in ACTIVE_STATES]
        if len(set(active_jobs)) != len(active_jobs):
            raise WalletWriteConflictError("Active job uniqueness conflict")
        self.database.wallets.update(self.wallets)
        self.database.entries.update(self.entries)
        self.database.reservations.update(self.reservations)
        self.database.commits += 1

    def close(self) -> None:
        for lock in reversed(self.acquired):
            lock.release()
