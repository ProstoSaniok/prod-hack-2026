from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, cast
from uuid import UUID

import pytest

from src.application.user.entity import UserId
from src.application.wallet import policy
from src.application.wallet.entity import (
    BusinessSource,
    LedgerEntryId,
    Reservation,
    ReservationId,
    ReservationState,
    SourceKind,
    Wallet,
    WalletChange,
)
from src.application.wallet.errors import (
    InsufficientFundsError,
    RewardNotEligibleError,
    WalletConflictError,
    WalletInvariantError,
)
from src.application.wallet.policy import (
    Allocation,
    ExpiredAllocation,
    PriorityJob,
    PublicationEvidence,
    QualityConfirmation,
)

AUTHOR_ID = UserId(UUID(int=1))
REVIEWER_ID = UserId(UUID(int=2))
EXPERT_ID = UserId(UUID(int=3))
OTHER_ID = UserId(UUID(int=4))
CASE_ID = UUID(int=5)
REVIEW_ID = UUID(int=6)
REVISION_ID = UUID(int=7)
JOB_ID = UUID(int=8)
ASSIGNMENT_ID = UUID(int=9)
RESERVATION_ID = ReservationId(UUID(int=10))
RESERVE_ENTRY_ID = LedgerEntryId(UUID(int=11))
SETTLEMENT_ENTRY_ID = LedgerEntryId(UUID(int=12))
TERMINAL_ENTRY_ID = LedgerEntryId(UUID(int=13))
SKILL_IDS = frozenset({UUID(int=14), UUID(int=15)})
OTHER_UUID = UUID(int=16)
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)

type Transition = Literal["settle", "release", "refund"]


class RewardPolicy(Protocol):
    def __call__(self, wallet: Wallet, evidence: PublicationEvidence, *, now: datetime) -> WalletChange: ...


def publication() -> PublicationEvidence:
    # T09/T10 authorize current per-skill rights and validate feedback/verdicts;
    # this is a server-derived DTO, never a client authorization claim.
    return PublicationEvidence(
        review_id=REVIEW_ID,
        revision_id=REVISION_ID,
        case_id=CASE_ID,
        author_id=AUTHOR_ID,
        reviewer_id=REVIEWER_ID,
        case_skill_ids=SKILL_IDS,
        published=True,
        valid_publication=True,
        approved=True,
        score=Decimal(70),
        quality_required=False,
    )


def confirmation() -> QualityConfirmation:
    return QualityConfirmation(
        revision_id=REVISION_ID,
        expert_id=EXPERT_ID,
        confirmed_skill_ids=SKILL_IDS,
        quality_score=Decimal(70),
    )


def reservation(state: ReservationState = ReservationState.HELD) -> Reservation:
    charged = state in {ReservationState.SETTLED, ReservationState.REFUNDED}
    terminal = state in {ReservationState.RELEASED, ReservationState.REFUNDED}
    return Reservation(
        id=RESERVATION_ID,
        owner_id=AUTHOR_ID,
        job_id=JOB_ID,
        state=state,
        reserve_entry_id=RESERVE_ENTRY_ID,
        created_at=NOW,
        assignment_id=ASSIGNMENT_ID if charged else None,
        settlement_entry_id=SETTLEMENT_ENTRY_ID if charged else None,
        terminal_entry_id=TERMINAL_ENTRY_ID if terminal else None,
    )


def apply_transition(transition: Transition, wallet: Wallet, hold: Reservation, *, now: datetime) -> WalletChange:
    if transition == "settle":
        allocation = Allocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, eligible=True)
        return policy.settle(wallet, hold, allocation, now=now)
    if transition == "release":
        return policy.release(wallet, hold, now=now)
    expiry = ExpiredAllocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, lease_expires_at=NOW)
    return policy.refund(wallet, hold, expiry, now=now)


def assert_change(
    change: WalletChange,
    before: Wallet,
    *,
    settled: int,
    reserved: int = 0,
    karma: int = 0,
) -> None:
    assert change.before is before
    assert change.after is not before
    assert change.after == replace(
        before,
        settled=before.settled + settled,
        reserved=before.reserved + reserved,
        karma=before.karma + karma,
        version=before.version + 1,
    )
    assert change.after.available == before.available + settled - reserved
    assert change.entry.owner_id == before.owner_id
    assert change.entry.created_at == NOW
    assert change.entry.sequence == before.version + 1
    assert (change.entry.settled_delta, change.entry.reserved_delta, change.entry.karma_delta) == (
        settled,
        reserved,
        karma,
    )


@pytest.mark.parametrize(("reward", "owner_id"), [(policy.reward_case, AUTHOR_ID)], ids=["case"])
@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(replace(publication(), published=False, score=Decimal(100)), id="unpublished-high-score"),
        pytest.param(replace(publication(), valid_publication=False, score=Decimal(100)), id="invalid-high-score"),
    ],
)
def test_rewards_reject_invalid_publication(
    reward: RewardPolicy, owner_id: UserId, evidence: PublicationEvidence
) -> None:
    with pytest.raises(RewardNotEligibleError):
        reward(Wallet(owner_id=owner_id), evidence, now=NOW)


@pytest.mark.parametrize(("reward", "owner_id"), [(policy.reward_case, AUTHOR_ID)], ids=["case"])
@pytest.mark.parametrize(
    "proof",
    [
        pytest.param(replace(confirmation(), revision_id=OTHER_UUID), id="wrong-revision"),
        pytest.param(replace(confirmation(), expert_id=AUTHOR_ID), id="author-as-expert"),
        pytest.param(replace(confirmation(), expert_id=REVIEWER_ID), id="reviewer-as-expert"),
        pytest.param(
            replace(confirmation(), confirmed_skill_ids=frozenset({UUID(int=14)})), id="partial-skill-coverage"
        ),
    ],
)
def test_required_quality_rejects_missing_or_invalid_confirmation(
    reward: RewardPolicy, owner_id: UserId, proof: QualityConfirmation | None
) -> None:
    evidence = replace(publication(), quality_required=True, confirmation=proof)

    with pytest.raises(RewardNotEligibleError):
        reward(Wallet(owner_id=owner_id), evidence, now=NOW)


@pytest.mark.parametrize(
    ("reward", "owner_id"),
    [
        (policy.reward_case, OTHER_ID),
        (policy.reward_review, OTHER_ID),
    ],
)
def test_rewards_reject_wrong_beneficiary(reward: RewardPolicy, owner_id: UserId) -> None:
    with pytest.raises(RewardNotEligibleError):
        reward(Wallet(owner_id=owner_id), publication(), now=NOW)


def test_case_source_is_per_participant_and_case_not_review() -> None:
    original = policy.reward_case(Wallet(owner_id=AUTHOR_ID), publication(), now=NOW)
    another_review = policy.reward_case(
        Wallet(owner_id=AUTHOR_ID), replace(publication(), review_id=OTHER_UUID), now=NOW
    )
    another_participant = policy.reward_case(
        Wallet(owner_id=OTHER_ID), replace(publication(), author_id=OTHER_ID), now=NOW
    )
    another_case = policy.reward_case(Wallet(owner_id=AUTHOR_ID), replace(publication(), case_id=OTHER_UUID), now=NOW)

    assert original.entry.source == another_review.entry.source
    assert original.entry.source != another_participant.entry.source
    assert original.entry.source != another_case.entry.source


def test_reserve_only_reduces_available_funds_and_records_hold_relationships() -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=15, reserved=5, karma=2, version=8)
    job = PriorityJob(job_id=JOB_ID, owner_id=AUTHOR_ID, waiting_primary=True)

    change = policy.reserve(wallet, RESERVATION_ID, job, now=NOW)

    assert_change(change, wallet, settled=0, reserved=5)
    assert change.entry.source == BusinessSource(kind=SourceKind.RESERVE, subject_id=RESERVATION_ID)
    assert change.entry.reservation_id == RESERVATION_ID
    assert change.entry.job_id == JOB_ID
    assert change.entry.related_entry_id is None
    assert change.entry.assignment_id is None
    assert change.reservation_before is None
    assert change.reservation_after == replace(reservation(), reserve_entry_id=change.entry.id)
    assert wallet == Wallet(owner_id=AUTHOR_ID, settled=15, reserved=5, karma=2, version=8)


@pytest.mark.parametrize(("settled", "reserved"), [(10, 5)])
def test_reserve_accepts_exactly_five_available_units(settled: int, reserved: int) -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=settled, reserved=reserved)
    job = PriorityJob(job_id=JOB_ID, owner_id=AUTHOR_ID, waiting_primary=True)

    change = policy.reserve(wallet, RESERVATION_ID, job, now=NOW)

    assert change.after.available == 0
    assert change.after.reserved == reserved + 5


@pytest.mark.parametrize(("settled", "reserved"), [(10, 6)])
def test_reserve_rejects_insufficient_available_funds(settled: int, reserved: int) -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=settled, reserved=reserved)
    job = PriorityJob(job_id=JOB_ID, owner_id=AUTHOR_ID, waiting_primary=True)

    with pytest.raises(InsufficientFundsError):
        policy.reserve(wallet, RESERVATION_ID, job, now=NOW)


@pytest.mark.parametrize(
    "job",
    [
        PriorityJob(job_id=JOB_ID, owner_id=OTHER_ID, waiting_primary=True),
        PriorityJob(job_id=JOB_ID, owner_id=AUTHOR_ID, waiting_primary=False),
    ],
    ids=["foreign-owner", "not-waiting-primary"],
)
def test_reserve_requires_owned_waiting_primary_job(job: PriorityJob) -> None:
    with pytest.raises(WalletConflictError):
        policy.reserve(Wallet(owner_id=AUTHOR_ID, settled=10), RESERVATION_ID, job, now=NOW)


def test_settle_charges_five_and_preserves_other_active_holds() -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=15, reserved=10, karma=2, version=8)
    hold = reservation()
    allocation = Allocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, eligible=True)

    change = policy.settle(wallet, hold, allocation, now=NOW)

    assert_change(change, wallet, settled=-5, reserved=-5)
    assert change.entry.source == BusinessSource(kind=SourceKind.SETTLE, subject_id=RESERVATION_ID)
    assert change.entry.related_entry_id == RESERVE_ENTRY_ID
    assert change.entry.reservation_id == RESERVATION_ID
    assert change.entry.job_id == JOB_ID
    assert change.entry.assignment_id == ASSIGNMENT_ID
    assert change.reservation_before is hold
    assert change.reservation_after == replace(
        hold, state=ReservationState.SETTLED, assignment_id=ASSIGNMENT_ID, settlement_entry_id=change.entry.id
    )
    assert hold == reservation()


@pytest.mark.parametrize(
    "allocation",
    [
        Allocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, eligible=False),
        Allocation(job_id=OTHER_UUID, assignment_id=ASSIGNMENT_ID, eligible=True),
    ],
    ids=["ineligible-reviewer", "different-job"],
)
def test_settle_rejects_ineligible_allocation_or_wrong_job(allocation: Allocation) -> None:
    with pytest.raises(WalletConflictError):
        policy.settle(Wallet(owner_id=AUTHOR_ID, settled=10, reserved=5), reservation(), allocation, now=NOW)


def test_release_restores_available_without_debiting_settled_balance() -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=15, reserved=10, karma=2, version=8)
    hold = reservation()

    change = policy.release(wallet, hold, now=NOW)

    assert_change(change, wallet, settled=0, reserved=-5)
    assert change.entry.source == BusinessSource(kind=SourceKind.RELEASE, subject_id=RESERVATION_ID)
    assert change.entry.related_entry_id == RESERVE_ENTRY_ID
    assert change.entry.reservation_id == RESERVATION_ID
    assert change.entry.job_id == JOB_ID
    assert change.entry.assignment_id is None
    assert change.reservation_before is hold
    assert change.reservation_after == replace(hold, state=ReservationState.RELEASED, terminal_entry_id=change.entry.id)
    assert hold == reservation()


@pytest.mark.parametrize("seconds_since_expiry", [0])
def test_refund_restores_exact_charge_at_or_after_lease_expiry(seconds_since_expiry: int) -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=10, reserved=5, karma=2, version=8)
    hold = reservation(ReservationState.SETTLED)
    expiry = ExpiredAllocation(
        job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, lease_expires_at=NOW - timedelta(seconds=seconds_since_expiry)
    )

    change = policy.refund(wallet, hold, expiry, now=NOW)

    assert_change(change, wallet, settled=5)
    assert change.entry.source == BusinessSource(kind=SourceKind.REFUND, subject_id=RESERVATION_ID)
    assert change.entry.related_entry_id == SETTLEMENT_ENTRY_ID
    assert change.entry.reservation_id == RESERVATION_ID
    assert change.entry.job_id == JOB_ID
    assert change.entry.assignment_id == ASSIGNMENT_ID
    assert change.reservation_before is hold
    assert change.reservation_after == replace(hold, state=ReservationState.REFUNDED, terminal_entry_id=change.entry.id)
    assert hold == reservation(ReservationState.SETTLED)


def test_refund_works_after_remaining_available_funds_have_been_spent() -> None:
    wallet = Wallet(owner_id=AUTHOR_ID)
    expiry = ExpiredAllocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, lease_expires_at=NOW)

    change = policy.refund(wallet, reservation(ReservationState.SETTLED), expiry, now=NOW)

    assert_change(change, wallet, settled=5)


@pytest.mark.parametrize(
    "expiry",
    [
        ExpiredAllocation(job_id=OTHER_UUID, assignment_id=ASSIGNMENT_ID, lease_expires_at=NOW),
        ExpiredAllocation(job_id=JOB_ID, assignment_id=OTHER_UUID, lease_expires_at=NOW),
        ExpiredAllocation(job_id=JOB_ID, assignment_id=ASSIGNMENT_ID, lease_expires_at=NOW + timedelta(microseconds=1)),
    ],
    ids=["different-job", "different-assignment", "not-yet-expired"],
)
def test_refund_rejects_wrong_job_assignment_or_early_expiry(expiry: ExpiredAllocation) -> None:
    with pytest.raises(WalletConflictError):
        policy.refund(Wallet(owner_id=AUTHOR_ID), reservation(ReservationState.SETTLED), expiry, now=NOW)


@pytest.mark.parametrize(
    ("transition", "state"),
    [
        ("release", ReservationState.RELEASED),
        ("refund", ReservationState.REFUNDED),
    ],
)
def test_invalid_and_terminal_reservation_transitions_reject(transition: Transition, state: ReservationState) -> None:
    wallet = Wallet(owner_id=AUTHOR_ID, settled=15, reserved=5)
    hold = reservation(state)

    with pytest.raises(WalletConflictError):
        apply_transition(transition, wallet, hold, now=NOW)

    assert hold == reservation(state)
    assert wallet == Wallet(owner_id=AUTHOR_ID, settled=15, reserved=5)


@pytest.mark.parametrize("transition", ["settle", "release"])
@pytest.mark.parametrize("reserved", [4])
def test_held_reservation_must_be_backed_by_reserved_funds(transition: Transition, reserved: int) -> None:
    with pytest.raises(WalletInvariantError):
        apply_transition(transition, Wallet(owner_id=AUTHOR_ID, settled=10, reserved=reserved), reservation(), now=NOW)


@pytest.mark.parametrize(("field", "value"), [("settled", -1), ("reserved", True), ("settled", Decimal("1.5"))])
def test_wallet_rejects_negative_or_noninteger_values(field: str, value: int | float | str | Decimal) -> None:
    values = {"settled": 10, "reserved": 0, "karma": 0, "version": 0}
    # Deliberately malformed runtime input must bypass the static integer contract.
    values[field] = cast(int, value)

    with pytest.raises(WalletInvariantError):
        Wallet(
            owner_id=AUTHOR_ID,
            settled=values["settled"],
            reserved=values["reserved"],
            karma=values["karma"],
            version=values["version"],
        )


def test_wallet_rejects_unbacked_reserved_balance() -> None:
    with pytest.raises(WalletInvariantError):
        Wallet(owner_id=AUTHOR_ID, settled=4, reserved=5)
