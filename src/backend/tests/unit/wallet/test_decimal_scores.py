from datetime import UTC, datetime
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import Literal, cast
from uuid import UUID

import pytest

from src.application.user.entity import UserId
from src.application.wallet import policy
from src.application.wallet.entity import Wallet, WalletChange
from src.application.wallet.errors import RewardNotEligibleError
from src.application.wallet.policy import PublicationEvidence, QualityConfirmation

AUTHOR_ID = UserId(UUID(int=1))
REVIEWER_ID = UserId(UUID(int=2))
EXPERT_ID = UserId(UUID(int=3))
REVISION_ID = UUID(int=4)
SKILL_IDS = frozenset({UUID(int=5)})
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)

type RewardKind = Literal["case", "review"]
type ScoreField = Literal["score", "quality_score"]


def publication(
    *, score: Decimal = Decimal(70), quality_score: Decimal = Decimal(70), approved: bool = True
) -> PublicationEvidence:
    # Fresh server-derived facts for pure policy tests; no DB or sibling fixtures.
    return PublicationEvidence(
        review_id=UUID(int=6),
        revision_id=REVISION_ID,
        case_id=UUID(int=7),
        author_id=AUTHOR_ID,
        reviewer_id=REVIEWER_ID,
        case_skill_ids=SKILL_IDS,
        published=True,
        valid_publication=True,
        approved=approved,
        score=score,
        quality_required=True,
        confirmation=QualityConfirmation(
            revision_id=REVISION_ID,
            expert_id=EXPERT_ID,
            confirmed_skill_ids=SKILL_IDS,
            quality_score=quality_score,
        ),
    )


def publication_with_value(field: ScoreField, value: Decimal) -> PublicationEvidence:
    if field == "score":
        return publication(score=value)
    return publication(quality_score=value)


def reward(kind: RewardKind, evidence: PublicationEvidence) -> WalletChange:
    owner_id = AUTHOR_ID if kind == "case" else REVIEWER_ID
    wallet = Wallet(owner_id=owner_id, settled=7, reserved=5, karma=2, version=8)
    if kind == "case":
        return policy.reward_case(wallet, evidence, now=NOW)
    return policy.reward_review(wallet, evidence, now=NOW)


def assert_integer_payout(change: WalletChange, kind: RewardKind) -> None:
    credit = 3 if kind == "case" else 5
    karma = 0 if kind == "case" else 1
    assert (change.after.settled, change.after.reserved, change.after.karma, change.after.version) == (
        7 + credit,
        5,
        2 + karma,
        9,
    )
    assert change.after.available == 2 + credit
    assert (change.entry.settled_delta, change.entry.reserved_delta, change.entry.karma_delta) == (credit, 0, karma)
    assert (change.before.settled, change.before.reserved, change.before.karma, change.before.version) == (7, 5, 2, 8)
    amounts = (
        change.after.settled,
        change.after.reserved,
        change.after.available,
        change.after.karma,
        change.after.version,
        change.entry.settled_delta,
        change.entry.reserved_delta,
        change.entry.karma_delta,
        change.entry.sequence,
    )
    assert all(type(amount) is int for amount in amounts)


@pytest.mark.parametrize(
    ("score", "eligible"),
    [(Decimal(70), True)],
)
def test_approved_case_uses_exact_decimal_seventy_gate(score: Decimal, *, eligible: bool) -> None:
    evidence = publication(score=score)

    if eligible:
        assert_integer_payout(reward("case", evidence), "case")
    else:
        with pytest.raises(RewardNotEligibleError):
            reward("case", evidence)

    assert evidence.score.as_tuple() == score.as_tuple()


@pytest.mark.parametrize("kind", ["case", "review"])
@pytest.mark.parametrize(
    ("quality_score", "eligible"),
    [
        (Decimal("69.999"), False),
    ],
)
def test_mandatory_quality_has_its_own_exact_gate(kind: RewardKind, quality_score: Decimal, *, eligible: bool) -> None:
    evidence = publication(score=Decimal(100), quality_score=quality_score)

    if eligible:
        assert_integer_payout(reward(kind, evidence), kind)
    else:
        with pytest.raises(RewardNotEligibleError):
            reward(kind, evidence)


@pytest.mark.parametrize("score", [Decimal("40.125")])
@pytest.mark.parametrize(
    ("quality_score", "eligible"),
    [(Decimal(70), True)],
)
def test_useful_negative_fractional_reviews_earn_five_and_one_only_after_quality_confirmation(
    score: Decimal, quality_score: Decimal, *, eligible: bool
) -> None:
    evidence = publication(score=score, quality_score=quality_score, approved=False)

    if eligible:
        assert_integer_payout(reward("review", evidence), "review")
    else:
        with pytest.raises(RewardNotEligibleError):
            reward("review", evidence)

    with pytest.raises(RewardNotEligibleError):
        reward("case", evidence)


@pytest.mark.parametrize("kind", ["case"])
@pytest.mark.parametrize("field", ["score"])
@pytest.mark.parametrize("value", [Decimal("100.000000000000000000000000000001")])
def test_decimal_values_just_outside_zero_to_one_hundred_are_rejected(
    kind: RewardKind, field: ScoreField, value: Decimal
) -> None:
    evidence = publication_with_value(field, value)

    with pytest.raises(RewardNotEligibleError):
        reward(kind, evidence)


@pytest.mark.parametrize("kind", ["case"])
@pytest.mark.parametrize("field", ["score"])
@pytest.mark.parametrize(
    "value",
    [True],
    ids=["true"],
)
def test_non_decimal_runtime_scores_are_not_coerced(kind: RewardKind, field: ScoreField, value: object) -> None:
    # Intentionally bypass only the trusted DTO's static Decimal contract.
    evidence = publication_with_value(field, cast(Decimal, value))

    with pytest.raises(RewardNotEligibleError):
        reward(kind, evidence)


@pytest.mark.parametrize("kind", ["review"])
@pytest.mark.parametrize("field", ["quality_score"])
@pytest.mark.parametrize("value", ["sNaN"])
@pytest.mark.parametrize("trap_invalid_operation", [True], ids=["trapped"])
def test_nonfinite_decimals_raise_domain_error_without_signaling_invalid_operation(
    kind: RewardKind, field: ScoreField, value: str, *, trap_invalid_operation: bool
) -> None:
    with localcontext() as context:
        context.traps[InvalidOperation] = trap_invalid_operation
        context.clear_flags()
        evidence = publication_with_value(field, Decimal(value))

        with pytest.raises(RewardNotEligibleError):
            reward(kind, evidence)

        assert not context.flags[InvalidOperation]


@pytest.mark.parametrize(
    ("kind", "field", "precision", "value", "at_least_seventy"),
    [
        ("case", "score", 1, Decimal("69.999999999999999999999999999999999999999999999999999999999999"), False),
        (
            "review",
            "quality_score",
            1,
            Decimal("70.000000000000000000000000000000000000000000000000000000000001"),
            True,
        ),
    ],
    ids=["just-below-seventy", "just-above-seventy"],
)
def test_long_decimal_scores_are_not_rounded_or_quantized_by_ambient_context(
    kind: RewardKind, field: ScoreField, precision: int, value: Decimal, *, at_least_seventy: bool
) -> None:
    evidence = publication_with_value(field, value)
    proof = evidence.confirmation
    assert proof is not None
    original_score = evidence.score.as_tuple()
    original_quality = proof.quality_score.as_tuple()
    eligible = at_least_seventy or (field == "score" and kind == "review")

    with localcontext() as context:
        context.prec = precision
        context.traps[InvalidOperation] = True
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.clear_flags()

        if eligible:
            assert_integer_payout(reward(kind, evidence), kind)
        else:
            with pytest.raises(RewardNotEligibleError):
                reward(kind, evidence)

        assert not context.flags[InvalidOperation]
        assert not context.flags[Inexact]
        assert not context.flags[Rounded]

    assert evidence.score.as_tuple() == original_score
    assert proof.quality_score.as_tuple() == original_quality
    actual_value = evidence.score if field == "score" else proof.quality_score
    assert actual_value.as_tuple() == value.as_tuple()
