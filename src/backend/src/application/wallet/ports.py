from typing import Protocol
from uuid import UUID

from src.application.user.entity import UserId
from src.application.wallet.entity import (
    BusinessSource,
    LedgerEntry,
    Reservation,
    ReservationId,
    Wallet,
    WalletChange,
)


class WalletTransaction(Protocol):
    """Port bound to the caller's single open database UoW/session.

    Lock order: operation/request -> domain job/assignment/review -> deterministic
    user-skill/grant/probation rows -> ALL affected wallets sorted by owner UUID.
    Caller owns the preceding lock groups. Acquire wallets once, hold through
    outer commit/rollback. No remote I/O while locked. Creation of zero wallets
    must serialize absent-row races.

    Implementations must use the same transaction for every method. There is
    deliberately no commit/rollback here. Errors abort the entire outer UoW.
    """

    async def lock_wallets(self, owner_ids: tuple[UserId, ...]) -> None:
        """Lock/create wallets in supplied sorted order, including first-time accounts."""
        ...

    async def get_wallet(self, owner_id: UserId) -> Wallet:
        """Return current locked snapshot, including this transaction's earlier changes."""
        ...

    async def find_entry(self, source: BusinessSource) -> LedgerEntry | None:
        """Global permanent source lookup; never expire keys or filter by recipient."""
        ...

    async def get_reservation(self, owner_id: UserId, reservation_id: ReservationId) -> Reservation | None:
        """Owner-scoped lookup; foreign and missing reservations both return None."""
        ...

    async def active_reservation(self, job_id: UUID) -> Reservation | None:
        """Return HELD or SETTLED reservation; shared job lock prevents purchase races."""
        ...

    async def apply(self, change: WalletChange) -> None:
        """Atomically flush entry, snapshot CAS and reservation CAS, WITHOUT COMMIT.

        Enforce permanent global source uniqueness, (owner, sequence) uniqueness,
        immutable history, expected wallet version/hold state and at most one
        HELD/SETTLED reservation per job. Use WalletWriteConflictError for failed
        CAS/unique writes; caller must abort/retry its whole transaction. Never
        rollback-and-continue or silently swallow uniqueness failures.

        Currency and karma occupy the same immutable entry and write boundary.
        Reads must see staged changes. Prior entries/terminal holds cannot be
        overwritten/deleted. No arbitrary balance/karma mutation API is needed.
        """
        ...
