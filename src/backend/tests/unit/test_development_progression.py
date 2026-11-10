from dataclasses import is_dataclass, replace
from decimal import (
    Clamped,
    Decimal,
    Inexact,
    Rounded,
    Subnormal,
    localcontext,
)
from fractions import Fraction
from uuid import uuid4

import pytest

from src.application.development.progression import (
    aggregate_skill_progression,
    apply_solution_progression,
    calculate_solution_contribution,
)
from src.application.development.progression_types import (
    PROGRESSION_POLICY_VERSION,
    AssessorSnapshot,
    ProgressionConflictError,
    ProgressionValidationError,
    QualityConfirmationSnapshot,
    SolutionContribution,
    SolutionProgressionInput,
)


@pytest.fixture
def source() -> SolutionProgressionInput:
    skill_id = uuid4()
    return SolutionProgressionInput(
        source_id=uuid4(),
        source_version=1,
        expected_source_version=1,
        participant_id=uuid4(),
        case_id=uuid4(),
        case_version=1,
        expected_case_version=1,
        policy_version=PROGRESSION_POLICY_VERSION,
        skill_id=skill_id,
        solution_score=Decimal(80),
        weight=Decimal("0.1"),
        verdict="approved",
        finalized=True,
        supervision_required=False,
        reviewer=AssessorSnapshot(assessor_id=uuid4(), skill_id=skill_id, version=1, expertise=Decimal(75)),
    )


def _supervised(source: SolutionProgressionInput) -> SolutionProgressionInput:
    return replace(
        source,
        supervision_required=True,
        confirmation=QualityConfirmationSnapshot(
            assessment_id=uuid4(),
            version=1,
            expected_version=1,
            source_id=source.source_id,
            source_version=source.source_version,
            case_version=source.case_version,
            policy_version=source.policy_version,
            decision="confirmed",
            quality_score=Decimal(70),
            expert=AssessorSnapshot(
                assessor_id=uuid4(),
                skill_id=source.skill_id,
                version=1,
                expertise=Decimal(100),
                confirmed_expert=True,
            ),
        ),
    )


def _contribution(source: SolutionProgressionInput) -> SolutionContribution:
    result = calculate_solution_contribution(source)
    assert result is not None
    return result


def _with_fields[T](snapshot: T, **changes: object) -> T:
    """Build boundary-test inputs, including deliberately mistyped field values."""
    if not is_dataclass(snapshot) or isinstance(snapshot, type):
        raise TypeError("Expected a dataclass snapshot")
    return replace(snapshot, **changes)


@pytest.mark.parametrize(
    ("score", "weight", "expertise", "expected"),
    [
        ("80", "0.1", "75", "9.2"),
    ],
)
def test_exact_formula_boundaries(
    source: SolutionProgressionInput, score: str, weight: str, expertise: str, expected: str
) -> None:
    source = replace(
        source,
        solution_score=Decimal(score),
        weight=Decimal(weight),
        reviewer=replace(source.reviewer, expertise=Decimal(expertise)),
    )
    result = _contribution(source)
    assert result.amount == Decimal(expected)
    assert result.source.solution_score == Decimal(score)
    assert result.source.weight == Decimal(weight)
    assert result.assessor == source.reviewer


@pytest.mark.parametrize("score", ["69.999999999999999999999999999999"])
def test_score_below_threshold_has_no_effect(source: SolutionProgressionInput, score: str) -> None:
    effect = apply_solution_progression(replace(source, solution_score=Decimal(score)))
    assert not effect.applied
    assert effect.contribution is None
    assert effect.before == effect.after
    assert effect.after.displayed_value == Decimal("0.00")


def test_finalization_and_approved_verdict_are_both_required(source: SolutionProgressionInput) -> None:
    assert calculate_solution_contribution(replace(source, finalized=False)) is None
    assert calculate_solution_contribution(replace(source, verdict="changes_requested")) is None


def test_supervised_uses_confirming_expert_not_reviewer_expertise(source: SolutionProgressionInput) -> None:
    source = _supervised(replace(source, reviewer=replace(source.reviewer, expertise=Decimal(0))))
    result = _contribution(source)
    assert result.amount == Decimal("9.6")
    assert source.confirmation is not None
    assert result.assessor == source.confirmation.expert


@pytest.mark.parametrize("quality", ["69.9999999999999999999999999999"])
def test_mandatory_quality_threshold_has_no_effect(source: SolutionProgressionInput, quality: str) -> None:
    source = _supervised(source)
    assert source.confirmation is not None
    source = replace(source, confirmation=replace(source.confirmation, quality_score=Decimal(quality)))
    assert calculate_solution_contribution(source) is None


def test_pending_missing_or_needs_revision_confirmation_has_no_effect(source: SolutionProgressionInput) -> None:
    source = _supervised(source)
    assert source.confirmation is not None
    for candidate in (
        replace(source, finalized=False),
        replace(source, confirmation=None),
        replace(source, confirmation=replace(source.confirmation, decision="needs_revision")),
    ):
        effect = apply_solution_progression(candidate)
        assert not effect.applied
        assert effect.contribution is None
        assert effect.after.uncapped_total == 0


def test_quality_score_never_becomes_solution_xp_score(source: SolutionProgressionInput) -> None:
    source = _supervised(source)
    assert source.confirmation is not None
    high_quality = replace(source, confirmation=replace(source.confirmation, quality_score=Decimal(100)))
    assert _contribution(source).amount == _contribution(high_quality).amount == Decimal("9.6")
    assert calculate_solution_contribution(replace(high_quality, solution_score=Decimal("69.999"))) is None
    assert calculate_solution_contribution(replace(high_quality, verdict="changes_requested")) is None


def test_supervision_snapshot_survives_reviewer_graduation(source: SolutionProgressionInput) -> None:
    source = _supervised(replace(source, reviewer=replace(source.reviewer, expertise=Decimal(25))))
    assert source.confirmation is not None
    source = replace(
        source,
        confirmation=replace(source.confirmation, expert=replace(source.confirmation.expert, expertise=Decimal(75))),
    )
    historical = _contribution(source)
    graduated_reviewer = replace(source.reviewer, expertise=Decimal(100), version=2, confirmed_expert=True)
    graduated = replace(source, reviewer=graduated_reviewer)
    assert _contribution(graduated).amount == historical.amount == Decimal("9.2")
    assert source.reviewer.expertise == Decimal(25)
    assert calculate_solution_contribution(replace(graduated, confirmation=None)) is None
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(graduated, existing=(historical,))


def test_sources_are_uncapped_and_survive_cap_and_removal(source: SolutionProgressionInput) -> None:
    source = replace(
        source,
        solution_score=Decimal(100),
        weight=Decimal("0.25"),
        reviewer=replace(source.reviewer, expertise=Decimal(100)),
    )
    contributions = tuple(_contribution(replace(source, case_id=uuid4(), source_id=uuid4())) for _ in range(5))
    result = aggregate_skill_progression(
        participant_id=source.participant_id, skill_id=source.skill_id, contributions=contributions
    )
    assert result.uncapped_total == Decimal(150)
    assert result.capped_value == Decimal(100)
    assert result.displayed_value == Decimal("100.00")
    assert len(result.contributions) == 5
    assert all(item.amount == Decimal(30) for item in result.contributions)
    assert sum(item.amount for item in result.contributions) == result.uncapped_total

    recalculated = aggregate_skill_progression(
        participant_id=source.participant_id, skill_id=source.skill_id, contributions=result.contributions[2:]
    )
    assert recalculated.uncapped_total == recalculated.capped_value == Decimal(90)
    assert sum(item.amount for item in recalculated.contributions) == Decimal(90)


def test_sources_are_not_rounded_before_aggregation(source: SolutionProgressionInput) -> None:
    source = replace(
        source,
        solution_score=Decimal(70),
        weight=Decimal("0.00007"),
        reviewer=replace(source.reviewer, expertise=Decimal(0)),
    )
    first = apply_solution_progression(source)
    assert first.after.uncapped_total == Decimal("0.0049")
    assert first.after.displayed_value == Decimal("0.00")
    second = apply_solution_progression(
        replace(source, case_id=uuid4(), source_id=uuid4()), existing=first.after.contributions
    )
    assert second.after.uncapped_total == Decimal("0.0098")
    assert second.after.displayed_value == Decimal("0.01")
    assert all(item.amount == Decimal("0.0049") for item in second.after.contributions)


@pytest.mark.parametrize(("score", "displayed"), [("92.05", "9.21")])
def test_display_rounding_is_half_up(source: SolutionProgressionInput, score: str, displayed: str) -> None:
    source = replace(source, solution_score=Decimal(score), reviewer=replace(source.reviewer, expertise=Decimal(0)))
    effect = apply_solution_progression(source)
    assert effect.after.displayed_value == Decimal(displayed)
    assert effect.after.displayed_value.as_tuple().exponent == -2
    assert effect.after.uncapped_total == Decimal(score) / 10


def test_cap_uses_exact_aggregate_before_display_rounding(source: SolutionProgressionInput) -> None:
    source = replace(
        source,
        solution_score=Decimal(100),
        weight=Decimal("0.25"),
        reviewer=replace(source.reviewer, expertise=Decimal(0)),
    )
    existing = tuple(_contribution(replace(source, case_id=uuid4(), source_id=uuid4())) for _ in range(3))
    below = apply_solution_progression(replace(source, weight=Decimal("0.2499999")), existing=existing)
    assert below.after.uncapped_total == below.after.capped_value == Decimal("99.99999")
    assert below.after.displayed_value == Decimal("100.00")
    above = apply_solution_progression(
        replace(source, reviewer=replace(source.reviewer, expertise=Decimal(100))), existing=existing
    )
    assert above.after.uncapped_total == Decimal(105)
    assert above.after.capped_value == Decimal(100)
    assert above.after.displayed_value == Decimal("100.00")
    assert sum(item.amount for item in above.after.contributions) == Decimal(105)


def test_identical_retries_are_noop_even_with_duplicate_existing_sources(source: SolutionProgressionInput) -> None:
    first = apply_solution_progression(source)
    assert first.applied
    assert first.before.uncapped_total == 0
    assert first.after.uncapped_total == Decimal("9.2")
    retry = apply_solution_progression(source, existing=first.after.contributions * 3)
    assert not retry.applied
    assert retry.before == retry.after
    assert len(retry.after.contributions) == 1
    assert retry.after.uncapped_total == Decimal("9.2")


@pytest.mark.parametrize("changed_field", ["source_id"])
def test_revisions_and_new_review_ids_do_not_add_another_business_contribution(
    source: SolutionProgressionInput, changed_field: str
) -> None:
    existing = (_contribution(source),)
    changes: dict[str, object] = {
        "source_id": uuid4(),
        "source_version": 2,
        "expected_source_version": 2,
        "solution_score": Decimal(90),
        "weight": Decimal("0.2"),
        "reviewer": replace(source.reviewer, version=2),
        "case_version": 2,
        "expected_case_version": 2,
    }
    fields = {
        "source_id": ("source_id",),
        "revision": ("source_version", "expected_source_version"),
        "score": ("solution_score",),
        "weight": ("weight",),
        "reviewer": ("reviewer",),
        "case_version": ("case_version", "expected_case_version"),
    }
    candidate = _with_fields(source, **{field: changes[field] for field in fields[changed_field]})
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(candidate, existing=existing)
    assert existing[0].amount == Decimal("9.2")


@pytest.mark.parametrize("changed_field", ["revision"])
def test_ineligible_changed_evidence_conflicts_without_replacing_existing_xp(
    source: SolutionProgressionInput, changed_field: str
) -> None:
    first = apply_solution_progression(source)
    changes: dict[str, dict[str, object]] = {
        "score": {"solution_score": Decimal(60)},
        "verdict": {"verdict": "changes_requested"},
        "pending": {"finalized": False},
        "missing_confirmation": {"supervision_required": True},
        "revision": {
            "source_version": 2,
            "expected_source_version": 2,
            "solution_score": Decimal(60),
            "verdict": "changes_requested",
        },
    }
    changed = _with_fields(source, **changes[changed_field])
    assert calculate_solution_contribution(changed) is None
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(changed, existing=first.after.contributions)
    assert first.after.uncapped_total == Decimal("9.2")
    assert first.after.contributions[0].source == source


def test_ineligible_source_reuse_conflicts_but_unrelated_pending_case_has_no_effect(
    source: SolutionProgressionInput,
) -> None:
    first = apply_solution_progression(source)
    pending = replace(source, case_id=uuid4(), finalized=False)
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(pending, existing=first.after.contributions)
    unrelated = apply_solution_progression(replace(pending, source_id=uuid4()), existing=first.after.contributions)
    assert not unrelated.applied
    assert unrelated.before == unrelated.after == first.after


def test_skills_and_participants_have_independent_totals(source: SolutionProgressionInput) -> None:
    first = apply_solution_progression(source)
    skill_id = uuid4()
    other_skill = replace(
        source, skill_id=skill_id, weight=Decimal("0.2"), reviewer=replace(source.reviewer, skill_id=skill_id)
    )
    second = apply_solution_progression(other_skill)
    assert first.after.uncapped_total == Decimal("9.2")
    assert second.after.uncapped_total == Decimal("18.4")
    assert first.contribution is not None
    assert second.contribution is not None
    assert first.contribution.key != second.contribution.key
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(other_skill, existing=first.after.contributions)
    with pytest.raises(ProgressionConflictError):
        apply_solution_progression(replace(source, participant_id=uuid4()), existing=first.after.contributions)


@pytest.mark.parametrize("field", ["source_version", "case_version"])
def test_stale_source_and_case_versions_are_rejected(source: SolutionProgressionInput, field: str) -> None:
    with pytest.raises(ProgressionConflictError):
        calculate_solution_contribution(_with_fields(source, **{field: 2}))


@pytest.mark.parametrize("mismatch", ["source", "revision"])
def test_stale_or_wrong_quality_source_is_rejected(source: SolutionProgressionInput, mismatch: str) -> None:
    source = _supervised(source)
    assert calculate_solution_contribution(source) is not None
    assert source.confirmation is not None
    if mismatch == "source":
        source = replace(source, confirmation=replace(source.confirmation, source_id=uuid4()))
    else:
        source = replace(source, source_version=2, expected_source_version=2)
    with pytest.raises(ProgressionConflictError):
        calculate_solution_contribution(source)


@pytest.mark.parametrize("bad_expert", ["unconfirmed", "wrong_skill", "reviewer"])
def test_confirmation_requires_an_independent_relevant_expert(
    source: SolutionProgressionInput, bad_expert: str
) -> None:
    source = _supervised(source)
    assert source.confirmation is not None
    expert = source.confirmation.expert
    if bad_expert == "unconfirmed":
        expert = replace(expert, confirmed_expert=False)
    elif bad_expert == "wrong_skill":
        expert = replace(expert, skill_id=uuid4())
    elif bad_expert == "reviewer":
        expert = replace(expert, assessor_id=source.reviewer.assessor_id)
    elif bad_expert == "participant":
        expert = replace(expert, assessor_id=source.participant_id)
    else:
        expert = replace(expert, version=0)
    with pytest.raises(ProgressionValidationError):
        calculate_solution_contribution(replace(source, confirmation=replace(source.confirmation, expert=expert)))


def test_reviewer_cannot_be_the_participant_or_an_unrelated_skill(source: SolutionProgressionInput) -> None:
    for reviewer in (
        replace(source.reviewer, assessor_id=source.participant_id),
        replace(source.reviewer, skill_id=uuid4()),
        replace(source.reviewer, version=0),
    ):
        with pytest.raises(ProgressionValidationError):
            calculate_solution_contribution(replace(source, reviewer=reviewer))


@pytest.mark.parametrize("field", ["amount"])
def test_aggregate_does_not_trust_forged_contribution_outputs(source: SolutionProgressionInput, field: str) -> None:
    contribution = _contribution(source)
    values: dict[str, object] = {
        "amount": Decimal(20),
        "assessor": replace(source.reviewer, expertise=Decimal(100)),
        "source": replace(source, finalized=False),
    }
    with pytest.raises(ProgressionConflictError):
        aggregate_skill_progression(
            participant_id=source.participant_id,
            skill_id=source.skill_id,
            contributions=(_with_fields(contribution, **{field: values[field]}),),
        )


def test_exact_formula_and_display_ignore_ambient_decimal_context(source: SolutionProgressionInput) -> None:
    source = replace(
        source,
        solution_score=Decimal("80.123456789012345678901234567890123456789"),
        weight=Decimal("0.123456789012345678901234567890123456789"),
        reviewer=replace(source.reviewer, expertise=Decimal("75.123456789012345678901234567890123456789")),
    )
    expected = apply_solution_progression(source)
    assert expected.contribution is not None
    oracle = Fraction(source.solution_score) * Fraction(source.weight) * (1 + Fraction(source.reviewer.expertise) / 500)
    assert Fraction(expected.contribution.amount) == oracle
    other_case = replace(source, case_id=uuid4(), source_id=uuid4())
    expected_second = apply_solution_progression(other_case, existing=expected.after.contributions)

    with localcontext() as context:
        # localcontext copies sticky caller flags; measure only signals from this calculation.
        context.clear_flags()
        context.prec = 1
        context.Emax = 1
        context.Emin = -1
        context.clamp = 1
        for signal in (Inexact, Rounded, Subnormal, Clamped):
            context.traps[signal] = True
        actual = apply_solution_progression(source)
        actual_second = apply_solution_progression(other_case, existing=actual.after.contributions)
        assert actual == expected
        assert actual_second == expected_second
        assert not any(context.flags.values())
