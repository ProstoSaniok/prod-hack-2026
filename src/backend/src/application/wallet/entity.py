from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from src.application.user.entity import UserId
from src.application.wallet.errors import WalletInvariantError

ReservationId = NewType("ReservationId", UUID)
LedgerEntryId = NewType("LedgerEntryId", UUID)


class SourceKind(StrEnum):
    WELCOME = "welcome"
    SUCCESSFUL_CASE = "successful_case"
    REVIEW_REWARD = "review_reward"
    RESERVE = "priority_reserve"
    SETTLE = "priority_settle"
    RELEASE = "priority_release"
    REFUND = "priority_refund"


@dataclass(frozen=True, kw_only=True, slots=True)
class BusinessSource:
    """Permanent structured key, never an HTTP idempotency key or revision ID.

    Subject: account / case / underlying review / reservation. Scope is the
    participant only for successful-case rewards; review keys are globally unique.
    """

    kind: SourceKind
    subject_id: UUID
    scope_id: UserId | None = None

    def __post_init__(self) -> None:
        if (self.kind == SourceKind.SUCCESSFUL_CASE) != (self.scope_id is not None):
            raise WalletInvariantError("Only successful-case sources require participant scope")


def require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise WalletInvariantError("Timezone-aware timestamps are required")


@dataclass(frozen=True, kw_only=True, slots=True)
class Wallet:
    owner_id: UserId
    settled: int = 0
    reserved: int = 0
    karma: int = 0
    version: int = 0

    def __post_init__(self) -> None:
        values = (self.settled, self.reserved, self.karma, self.version)
        if any(type(value) is not int or value < 0 for value in values) or self.reserved > self.settled:
            raise WalletInvariantError("Wallet amounts/version must be nonnegative integers with sufficient backing")

    @property
    def available(self) -> int:
        return self.settled - self.reserved


class ReservationState(StrEnum):
    HELD = "held"
    SETTLED = "settled"
    RELEASED = "released"
    REFUNDED = "refunded"


@dataclass(frozen=True, kw_only=True, slots=True)
class Reservation:
    id: ReservationId
    owner_id: UserId
    job_id: UUID
    state: ReservationState
    reserve_entry_id: LedgerEntryId
    created_at: datetime
    assignment_id: UUID | None = None
    settlement_entry_id: LedgerEntryId | None = None
    terminal_entry_id: LedgerEntryId | None = None

    def __post_init__(self) -> None:
        require_aware(self.created_at)
        charged = self.state in {ReservationState.SETTLED, ReservationState.REFUNDED}
        terminal = self.state in {ReservationState.RELEASED, ReservationState.REFUNDED}
        if (
            charged != (self.assignment_id is not None)
            or charged != (self.settlement_entry_id is not None)
            or terminal != (self.terminal_entry_id is not None)
        ):
            raise WalletInvariantError("Reservation state and financial relationships disagree")


@dataclass(frozen=True, kw_only=True, slots=True)
class LedgerEntry:
    """Append-only history includes holds/releases, not only settled balance changes."""

    id: LedgerEntryId
    owner_id: UserId
    source: BusinessSource
    created_at: datetime
    sequence: int
    settled_delta: int
    reserved_delta: int
    karma_delta: int
    related_entry_id: LedgerEntryId | None = None
    reservation_id: ReservationId | None = None
    job_id: UUID | None = None
    assignment_id: UUID | None = None
    case_id: UUID | None = None
    origin_revision_id: UUID | None = None

    def __post_init__(self) -> None:
        require_aware(self.created_at)


@dataclass(frozen=True, kw_only=True, slots=True)
class WalletChange:
    """One atomic append + snapshot CAS + optional hold transition; never a commit."""

    before: Wallet
    after: Wallet
    entry: LedgerEntry
    reservation_before: Reservation | None = None
    reservation_after: Reservation | None = None
