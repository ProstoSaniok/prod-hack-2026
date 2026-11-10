"""Pure P02 policies; no persistence, authorization lookup or transaction claims.

The finalization caller must obtain authoritative snapshots/expected versions and
persist all effects in its own transaction with permanent business uniqueness.
Changed revisions/sources conflict rather than silently implementing T15 rechecks.
"""

from decimal import Decimal
from uuid import UUID

from src.application.development.progression_types import (
    PROGRESSION_POLICY_VERSION,
    AssessorSnapshot,
    ContributionKey,
    ProgressionConflictError,
    ProgressionEffect,
    ProgressionValidationError,
    QualityConfirmationSnapshot,
    SkillProgression,
    SolutionContribution,
    SolutionProgressionInput,
)


def calculate_solution_contribution(source: SolutionProgressionInput) -> SolutionContribution | None:
    """Return exact uncapped XP, or no effect for an ineligible result.

    Missing/non-confirming mandatory quality evidence never releases XP, even if
    the caller marks the solution finalized. Invalid/stale evidence raises.
    """
    _validate_source(source)
    if not source.finalized or source.verdict != "approved" or source.solution_score < Decimal(70):
        return None

    assessor = source.reviewer
    if source.supervision_required:
        confirmation = source.confirmation
        if confirmation is None or confirmation.decision != "confirmed" or confirmation.quality_score < Decimal(70):
            return None
        assessor = confirmation.expert

    bonus = _exact_product(assessor.expertise, Decimal("0.002"))
    multiplier = _exact_sum((Decimal(1), bonus))
    amount = _exact_product(_exact_product(source.solution_score, source.weight), multiplier)
    return SolutionContribution(source=source, assessor=assessor, amount=amount)


def aggregate_skill_progression(
    *,
    participant_id: UUID,
    skill_id: UUID,
    contributions: tuple[SolutionContribution, ...] = (),
) -> SkillProgression:
    """Deduplicate identical evidence, sum exact sources, cap, then display-round.

    One effective contribution is allowed per participant/case/skill regardless
    of request/review/revision IDs. Different evidence for that key is a conflict,
    not another increment or an implicit correction. Source amounts are checked
    against their snapshots, rather than trusted as arbitrary XP counters.
    """
    _require_uuid(participant_id)
    _require_uuid(skill_id)
    if not isinstance(contributions, tuple):
        raise ProgressionValidationError("Contributions must be an immutable tuple")

    by_key: dict[ContributionKey, SolutionContribution] = {}
    source_keys: dict[UUID, ContributionKey] = {}
    for contribution in contributions:
        _validate_contribution(contribution)
        key = contribution.key
        if key.participant_id != participant_id or key.skill_id != skill_id:
            raise ProgressionConflictError("Contributions must belong to the requested participant and skill")
        previous = by_key.get(key)
        if previous is not None and previous != contribution:
            raise ProgressionConflictError("Participant/case/skill already has different effective evidence")
        previous_key = source_keys.get(contribution.source.source_id)
        if previous_key is not None and previous_key != key:
            raise ProgressionConflictError("A review source cannot be reused for another case")
        by_key[key] = contribution
        source_keys[contribution.source.source_id] = key

    effective = tuple(sorted(by_key.values(), key=lambda item: item.key.case_id.int))
    total = _exact_sum(tuple(item.amount for item in effective))
    capped = min(Decimal(100), total)
    return SkillProgression(
        participant_id=participant_id,
        skill_id=skill_id,
        contributions=effective,
        uncapped_total=total,
        capped_value=capped,
        displayed_value=_display_half_up(capped),
    )


def apply_solution_progression(
    source: SolutionProgressionInput,
    *,
    existing: tuple[SolutionContribution, ...] = (),
) -> ProgressionEffect:
    """Explain a pure before/after change; identical business replay is a no-op."""
    contribution = calculate_solution_contribution(source)
    before = aggregate_skill_progression(
        participant_id=source.participant_id,
        skill_id=source.skill_id,
        contributions=existing,
    )
    for previous in before.contributions:
        if previous.key == source.key and previous.source != source:
            raise ProgressionConflictError("Participant/case/skill already has different effective evidence")
        if previous.source.source_id == source.source_id and previous.key != source.key:
            raise ProgressionConflictError("A review source cannot be reused for another case")
    if contribution is None:
        return ProgressionEffect(before=before, after=before, contribution=None, applied=False)
    after = aggregate_skill_progression(
        participant_id=source.participant_id,
        skill_id=source.skill_id,
        contributions=(*before.contributions, contribution),
    )
    return ProgressionEffect(
        before=before,
        after=after,
        contribution=contribution,
        applied=len(after.contributions) > len(before.contributions),
    )


def _require_uuid(value: object) -> None:
    if not isinstance(value, UUID):
        raise ProgressionValidationError("References must be UUIDs")


def _require_version(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ProgressionValidationError("Snapshot versions must be positive integers")


def _require_decimal(value: object, *, maximum: Decimal, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ProgressionValidationError("Numeric inputs must be finite Decimals, never floats")
    if value < 0 or value > maximum or (positive and value == 0):
        raise ProgressionValidationError("Numeric input is outside the policy range")


def _require_flag(value: object) -> None:
    if type(value) is not bool:
        raise ProgressionValidationError("Finalization, supervision and expert rights must be explicit booleans")


def _require_choice(value: object, allowed: tuple[str, ...]) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ProgressionValidationError("Unknown solution verdict or quality decision")


def _validate_assessor(snapshot: AssessorSnapshot, source: SolutionProgressionInput) -> None:
    if not isinstance(snapshot, AssessorSnapshot):
        raise ProgressionValidationError("An immutable assessor snapshot is required")
    _require_uuid(snapshot.assessor_id)
    _require_uuid(snapshot.skill_id)
    _require_version(snapshot.version)
    _require_decimal(snapshot.expertise, maximum=Decimal(100))
    _require_flag(snapshot.confirmed_expert)
    if snapshot.skill_id != source.skill_id or snapshot.assessor_id == source.participant_id:
        raise ProgressionValidationError("Assessor must be independent of the participant and relevant to the skill")


def _validate_confirmation(confirmation: QualityConfirmationSnapshot, source: SolutionProgressionInput) -> None:
    if not isinstance(confirmation, QualityConfirmationSnapshot):
        raise ProgressionValidationError("An immutable quality snapshot is required")
    _require_uuid(confirmation.assessment_id)
    _require_uuid(confirmation.source_id)
    for version in (
        confirmation.version,
        confirmation.expected_version,
        confirmation.source_version,
        confirmation.case_version,
    ):
        _require_version(version)
    if (
        confirmation.version != confirmation.expected_version
        or confirmation.source_id != source.source_id
        or confirmation.source_version != source.source_version
        or confirmation.case_version != source.case_version
        or confirmation.policy_version != source.policy_version
    ):
        raise ProgressionConflictError("Quality evidence targets a different source, revision, case or policy version")
    _require_choice(confirmation.decision, ("confirmed", "needs_revision"))
    _require_decimal(confirmation.quality_score, maximum=Decimal(100))
    _validate_assessor(confirmation.expert, source)
    if not confirmation.expert.confirmed_expert or confirmation.expert.assessor_id == source.reviewer.assessor_id:
        raise ProgressionValidationError("Confirmation requires an independent relevant confirmed expert")


def _validate_source(source: SolutionProgressionInput) -> None:
    if not isinstance(source, SolutionProgressionInput):
        raise ProgressionValidationError("An immutable solution progression input is required")
    for reference in (source.source_id, source.participant_id, source.case_id, source.skill_id):
        _require_uuid(reference)
    for version in (
        source.source_version,
        source.expected_source_version,
        source.case_version,
        source.expected_case_version,
    ):
        _require_version(version)
    if source.policy_version != PROGRESSION_POLICY_VERSION:
        raise ProgressionConflictError("Unsupported progression policy version")
    if source.source_version != source.expected_source_version or source.case_version != source.expected_case_version:
        raise ProgressionConflictError("Stale review source or case version")
    _require_decimal(source.solution_score, maximum=Decimal(100))
    _require_decimal(source.weight, maximum=Decimal("0.25"), positive=True)
    _require_choice(source.verdict, ("approved", "changes_requested"))
    _require_flag(source.finalized)
    _require_flag(source.supervision_required)
    _validate_assessor(source.reviewer, source)
    if source.confirmation is not None:
        if not source.supervision_required:
            raise ProgressionValidationError("Unsupervised sources cannot attach mandatory confirmation evidence")
        _validate_confirmation(source.confirmation, source)


def _validate_contribution(contribution: SolutionContribution) -> None:
    if not isinstance(contribution, SolutionContribution):
        raise ProgressionValidationError("An immutable source contribution is required")
    _require_decimal(contribution.amount, maximum=Decimal(30), positive=True)
    expected = calculate_solution_contribution(contribution.source)
    if expected is None or expected != contribution:
        raise ProgressionConflictError("Contribution does not match its immutable solution evidence")


def _components(value: Decimal) -> tuple[int, int]:
    """Extract coefficient/exponent without context-sensitive Decimal operations."""
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):
        raise ProgressionValidationError("Exact arithmetic requires finite decimals")
    coefficient = int(Decimal((parts.sign, parts.digits, 0)))
    return (coefficient, parts.exponent) if coefficient else (0, 0)


def _from_components(coefficient: int, exponent: int) -> Decimal:
    # Decimal(int) and Decimal(tuple) are exact even under a low-precision or
    # trapping context. Avoid str(int), which has Python's digit-count limit.
    parts = Decimal(coefficient).as_tuple()
    return Decimal((parts.sign, parts.digits, exponent))


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    left_coefficient, left_exponent = _components(left)
    right_coefficient, right_exponent = _components(right)
    return _from_components(left_coefficient * right_coefficient, left_exponent + right_exponent)


def _exact_sum(values: tuple[Decimal, ...]) -> Decimal:
    parts = tuple(_components(value) for value in values)
    exponent = min((part[1] for part in parts), default=0)
    coefficient = sum(part[0] * 10 ** (part[1] - exponent) for part in parts)
    return _from_components(coefficient, exponent)


def _display_half_up(value: Decimal) -> Decimal:
    if value < Decimal("0.005"):
        return Decimal("0.00")
    coefficient, exponent = _components(value)
    if exponent >= -2:
        return _from_components(coefficient * 10 ** (exponent + 2), -2)
    scale = 10 ** (-exponent - 2)
    displayed, remainder = divmod(coefficient, scale)
    if remainder * 2 >= scale:
        displayed += 1
    return _from_components(displayed, -2)
