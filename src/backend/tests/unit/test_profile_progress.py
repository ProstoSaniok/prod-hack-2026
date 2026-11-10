from decimal import Decimal, Inexact, localcontext
from uuid import uuid4

from src.application.development.entity import SkillGrade
from src.application.development.policy_types import Grade, GradeEvidenceInput, ProbationSnapshot
from src.application.profile.progression import next_grade


def test_next_grade_preserves_exact_positive_residue_without_inventing_promotion() -> None:
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        progress = next_grade(SkillGrade.PERFORMER, Decimal("24.999999999999999999999999999999"), GradeEvidenceInput())
    assert progress is not None
    assert progress.xp_remaining == Decimal("0.000000000000000000000000000001")
    assert progress.grade == SkillGrade.REVIEW_MINUS
    assert progress.successful_cases == 0
    assert progress.previous_probation_passed is True
    ready_xp = next_grade(SkillGrade.REVIEW_MINUS, Decimal(100), GradeEvidenceInput())
    assert ready_xp is not None
    assert ready_xp.xp_remaining == 0
    assert ready_xp.previous_probation_passed is False
    assert ready_xp.grade == SkillGrade.REVIEW_PLUS
    assert next_grade(SkillGrade.EXPERT, Decimal(100), GradeEvidenceInput()) is None


def test_expert_progress_requires_both_prior_probations() -> None:
    owner, skill = uuid4(), uuid4()
    minus = ProbationSnapshot(
        probation_id=uuid4(),
        user_id=owner,
        skill_id=skill,
        grade=Grade.REVIEW_MINUS,
        confirmed_review_ids=frozenset(uuid4() for _ in range(3)),
    )
    plus = ProbationSnapshot(
        probation_id=uuid4(),
        user_id=owner,
        skill_id=skill,
        grade=Grade.REVIEW_PLUS,
        confirmed_review_ids=frozenset(uuid4() for _ in range(3)),
    )
    waiting = next_grade(SkillGrade.REVIEW_PLUS, Decimal("58.25"), GradeEvidenceInput(probations=(minus,)))
    assert waiting is not None
    assert waiting.xp_remaining == Decimal("16.75")
    assert waiting.previous_probation_passed is False
    passed = next_grade(SkillGrade.REVIEW_PLUS, Decimal(75), GradeEvidenceInput(probations=(minus, plus)))
    assert passed is not None
    assert passed.previous_probation_passed
