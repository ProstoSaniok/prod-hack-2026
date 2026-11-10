"""Read-only progress to the next stored grade; this never grants reviewer rights."""

from decimal import Decimal, localcontext

from src.application.development.entity import SkillGrade
from src.application.development.policy_types import Grade, GradeEvidenceInput
from src.application.profile.queries import DevelopmentPolicy, NextGrade


def next_grade(grade: SkillGrade, total: Decimal, evidence: GradeEvidenceInput) -> NextGrade | None:
    gates = DevelopmentPolicy().grade_gates
    index = next(index for index, gate in enumerate(gates) if gate.grade == grade)
    if index == len(gates) - 1:
        return None
    target = gates[index + 1]
    threshold = Decimal(target.minimum_value)
    remaining = Decimal(0)
    if total < threshold:
        # Preserve a positive residue at a rounded display boundary, regardless of
        # Decimal's ambient precision (the persisted aggregate is uncapped Numeric).
        exponent = total.as_tuple().exponent
        if not isinstance(exponent, int):
            raise ValueError("Expected finite skill XP")
        with localcontext() as context:
            context.prec = max(len(total.as_tuple().digits), 3 - exponent) + 2
            remaining = threshold - total
    passed = {probation.grade for probation in evidence.probations if probation.passed}
    required = (
        {Grade.REVIEW_MINUS, Grade.REVIEW_PLUS}
        if target.grade == SkillGrade.EXPERT
        else {Grade.REVIEW_MINUS}
        if target.previous_probation_required
        else set()
    )
    return NextGrade(
        grade=target.grade,
        xp_remaining=remaining,
        successful_cases=min(target.successful_cases, len({case.case_id for case in evidence.successful_cases})),
        previous_probation_passed=required <= passed,
    )
