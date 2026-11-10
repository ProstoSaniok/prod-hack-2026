from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.application.development.policy import (
    advance_probation,
    assess_expert_readiness,
    assess_review_quality,
    request_expert_readiness,
    require_grade_entry,
    require_solution_reviewer,
    requires_quality_confirmation,
    start_probation,
)
from src.application.development.policy_types import (
    CaseSkillInput,
    Grade,
    GradeEvidenceInput,
    Grant,
    PolicyViolationError as PolicyViolation,
    ProbationBinding,
    ProbationSnapshot,
    QualityAssessmentInput,
    QualityAssignmentInput,
    QualityDecision,
    QualityEvidence,
    ReadinessDecision,
    ReviewSnapshot,
    SkillAuthorityInput,
    SuccessfulCaseInput,
)


def authority(
    user_id: UUID,
    skill_id: UUID,
    *,
    value: str = "80",
    grade: Grade = Grade.EXPERT,
    grant: Grant = Grant.CONFIRMED,
) -> SkillAuthorityInput:
    return SkillAuthorityInput(
        user_id=user_id,
        skill_id=skill_id,
        value=Decimal(value),
        grade=grade,
        grant=grant,
        expert_confirmed=grade == Grade.EXPERT,
    )


def probation_for(user_id: UUID, skill_id: UUID, grade: Grade = Grade.REVIEW_MINUS) -> ProbationSnapshot:
    return ProbationSnapshot(probation_id=uuid4(), user_id=user_id, skill_id=skill_id, grade=grade)


def review_for(probations: tuple[ProbationSnapshot, ...]) -> ReviewSnapshot:
    return ReviewSnapshot(
        review_id=uuid4(),
        revision_id=uuid4(),
        author_id=uuid4(),
        reviewer_id=probations[0].user_id,
        skill_ids=frozenset(item.skill_id for item in probations),
        probation_bindings=tuple(
            ProbationBinding(probation_id=item.probation_id, skill_id=item.skill_id, grade=item.grade)
            for item in probations
        ),
    )


def assessment_for(review: ReviewSnapshot, expert_id: UUID | None = None) -> QualityAssessmentInput:
    return QualityAssessmentInput(
        assignment=QualityAssignmentInput(
            assignment_id=uuid4(),
            review_id=review.review_id,
            revision_id=review.revision_id,
            expert_id=expert_id or uuid4(),
            active=True,
        ),
        decision=QualityDecision.CONFIRMED,
        quality_score=Decimal(70),
        feedback="Explain the join cardinality, add a regression test, then resubmit.",
    )


def confirmation_for(review: ReviewSnapshot) -> QualityEvidence:
    assessment = assessment_for(review)
    rights = tuple(authority(assessment.assignment.expert_id, skill_id) for skill_id in review.skill_ids)
    return assess_review_quality(review, assessment, rights)


def evidence_for(candidate: SkillAuthorityInput) -> GradeEvidenceInput:
    minus = probation_for(candidate.user_id, candidate.skill_id)
    plus = probation_for(candidate.user_id, candidate.skill_id, Grade.REVIEW_PLUS)
    confirmations = tuple(confirmation_for(review_for((minus,))) for _ in range(3))
    for confirmation in confirmations:
        minus = advance_probation(minus, confirmation.review, confirmation)
    for _ in range(3):
        review = review_for((plus,))
        confirmation = confirmation_for(review)
        plus = advance_probation(plus, review, confirmation)
    return GradeEvidenceInput(
        successful_cases=tuple(
            SuccessfulCaseInput(
                participant_id=candidate.user_id,
                case_id=uuid4(),
                skill_id=candidate.skill_id,
                solution_score=Decimal(70),
                approved=True,
                finalized=True,
            )
            for _ in range(2)
        ),
        quality_assessments=confirmations,
        probations=(minus, plus),
    )


@pytest.mark.parametrize(
    ("required", "grade", "value", "allowed"),
    [
        ("25", Grade.REVIEW_MINUS, "24.999", False),
        ("25", Grade.REVIEW_MINUS, "25", True),
        ("50", Grade.REVIEW_MINUS, "100", False),
        ("50", Grade.REVIEW_PLUS, "50", True),
        ("75", Grade.REVIEW_PLUS, "100", False),
        ("75", Grade.EXPERT, "75", True),
    ],
)
def test_solution_per_skill_value_and_grade_floors(required: str, grade: Grade, value: str, *, allowed: bool) -> None:
    reviewer, author, skill = uuid4(), uuid4(), uuid4()
    requirements = (CaseSkillInput(skill_id=skill, required_value=Decimal(required), author_grade=Grade.PERFORMER),)
    rights = (authority(reviewer, skill, grade=grade, value=value),)
    if allowed:
        assert require_solution_reviewer(reviewer, author, requirements, rights) == frozenset()
    else:
        with pytest.raises(PolicyViolation):
            require_solution_reviewer(reviewer, author, requirements, rights)


@pytest.mark.parametrize(
    ("author_grade", "reviewer_grade", "allowed"),
    [
        (Grade.REVIEW_MINUS, Grade.REVIEW_MINUS, False),
        (Grade.EXPERT, Grade.EXPERT, True),
    ],
)
def test_author_floor_is_per_skill_and_capped_at_expert(
    author_grade: Grade, reviewer_grade: Grade, *, allowed: bool
) -> None:
    reviewer, author, skill = uuid4(), uuid4(), uuid4()
    requirement = CaseSkillInput(skill_id=skill, required_value=Decimal(0), author_grade=author_grade)
    rights = (authority(reviewer, skill, grade=reviewer_grade),)
    if allowed:
        require_solution_reviewer(reviewer, author, (requirement,), rights)
    else:
        with pytest.raises(PolicyViolation):
            require_solution_reviewer(reviewer, author, (requirement,), rights)


def test_multi_skill_solution_requires_all_current_grants_and_independent_expert_rights() -> None:
    reviewer, author, sql, api = uuid4(), uuid4(), uuid4(), uuid4()
    requirements = (
        CaseSkillInput(skill_id=sql, required_value=Decimal(80), author_grade=Grade.PERFORMER),
        CaseSkillInput(skill_id=api, required_value=Decimal(40), author_grade=Grade.PERFORMER),
    )
    expert = authority(reviewer, sql)
    beginner = authority(reviewer, api, value="40", grade=Grade.REVIEW_MINUS, grant=Grant.SUPERVISED)
    assert require_solution_reviewer(reviewer, author, requirements, (expert, beginner)) == frozenset({api})
    invalid_rights = (
        (expert,),
        (replace(expert, expert_confirmed=False), beginner),
        (replace(expert, grant=Grant.REVOKED), beginner),
        (expert, replace(beginner, grant=Grant.REVOKED)),
        (expert, replace(beginner, user_id=uuid4())),
        (expert, expert, beginner),
    )
    for rights in invalid_rights:
        with pytest.raises(PolicyViolation):
            require_solution_reviewer(reviewer, author, requirements, rights)
    for invalid_requirements in ((), (requirements[0], requirements[0])):
        with pytest.raises(PolicyViolation):
            require_solution_reviewer(reviewer, author, invalid_requirements, (expert, beginner))
    with pytest.raises(PolicyViolation):
        require_solution_reviewer(reviewer, reviewer, requirements, (expert, beginner))


def test_quality_requires_all_relevant_experts_not_review_plus_or_numerical_expert() -> None:
    probations = (probation_for(uuid4(), uuid4()),)
    review = review_for(probations)
    review = replace(review, skill_ids=review.skill_ids | {uuid4()})
    assessment = assessment_for(review)
    rights = tuple(authority(assessment.assignment.expert_id, skill) for skill in sorted(review.skill_ids))
    for invalid in (
        rights[:1],
        (replace(rights[0], grade=Grade.REVIEW_PLUS), rights[1]),
        (replace(rights[0], expert_confirmed=False), rights[1]),
        (replace(rights[0], grant=Grant.REVOKED), rights[1]),
        (replace(rights[0], value=Decimal("74.999")), rights[1]),
    ):
        with pytest.raises(PolicyViolation):
            assess_review_quality(review, assessment, invalid)
    result = assess_review_quality(review, assessment, rights)
    assert result.quality_score == 70
    assert {item.skill_id for item in result.expert_snapshots} == review.skill_ids


def test_quality_rejects_conflicts_stale_revision_inactive_and_wrong_assignment() -> None:
    review = review_for((probation_for(uuid4(), uuid4()),))
    assessment = assessment_for(review)
    for assignment in (
        replace(assessment.assignment, expert_id=review.author_id),
        replace(assessment.assignment, expert_id=review.reviewer_id),
        replace(assessment.assignment, revision_id=uuid4()),
        replace(assessment.assignment, review_id=uuid4()),
        replace(assessment.assignment, active=False),
    ):
        with pytest.raises(PolicyViolation):
            assess_review_quality(review, replace(assessment, assignment=assignment), ())


def test_quality_threshold_feedback_and_needs_revision_leave_probation_unchanged() -> None:
    probation = probation_for(uuid4(), uuid4())
    review = review_for((probation,))
    assessment = assessment_for(review)
    rights = (authority(assessment.assignment.expert_id, probation.skill_id),)
    with pytest.raises(PolicyViolation):
        assess_review_quality(review, replace(assessment, quality_score=Decimal("69.999")), rights)
    with pytest.raises(PolicyViolation):
        replace(assessment, feedback=" \n ")
    for score in ("0", "69.999", "70", "100"):
        revision = replace(assessment, decision=QualityDecision.NEEDS_REVISION, quality_score=Decimal(score))
        evidence = assess_review_quality(review, revision, rights)
        assert advance_probation(probation, review, evidence) is probation
        assert evidence.feedback == assessment.feedback


def test_three_distinct_underlying_reviews_pass_each_grade_and_revision_counts_once() -> None:
    for grade in (Grade.REVIEW_MINUS, Grade.REVIEW_PLUS):
        probation = probation_for(uuid4(), uuid4(), grade)
        original = probation
        review = review_for((probation,))
        confirmation = confirmation_for(review)
        probation = advance_probation(probation, review, confirmation)
        assert len(probation.confirmed_review_ids) == 1
        assert advance_probation(probation, review, confirmation) is probation
        revised = replace(review, revision_id=uuid4())
        with pytest.raises(PolicyViolation):
            advance_probation(probation, revised, confirmation)
        assert advance_probation(probation, revised, confirmation_for(revised)) is probation
        for count in (2, 3):
            another = review_for((probation,))
            probation = advance_probation(probation, another, confirmation_for(another))
            assert len(probation.confirmed_review_ids) == count
            assert probation.passed == (count == 3)
        fourth = review_for((probation,))
        assert advance_probation(probation, fourth, confirmation_for(fourth)) is probation
        assert not original.confirmed_review_ids


def test_probation_skills_epochs_and_sticky_supervision_are_independent() -> None:
    reviewer = uuid4()
    sql, api = probation_for(reviewer, uuid4()), probation_for(reviewer, uuid4())
    review = review_for((sql, api))
    confirmation = confirmation_for(review)
    assert advance_probation(sql, review, confirmation).confirmed_review_ids == frozenset({review.review_id})
    assert advance_probation(api, review, confirmation).confirmed_review_ids == frozenset({review.review_id})
    for wrong in (
        replace(sql, probation_id=uuid4()),
        replace(sql, grade=Grade.REVIEW_PLUS),
        replace(sql, user_id=uuid4()),
        replace(sql, skill_id=uuid4()),
    ):
        with pytest.raises(PolicyViolation):
            advance_probation(wrong, review, confirmation)
    graduated = replace(sql, confirmed_review_ids=frozenset(uuid4() for _ in range(3)))
    assert graduated.passed
    assert requires_quality_confirmation(review)
    assert advance_probation(graduated, review, confirmation) is graduated
    assert not requires_quality_confirmation(replace(review, probation_bindings=()))


@pytest.mark.parametrize(
    ("grade", "target", "threshold"),
    [
        (Grade.PERFORMER, Grade.REVIEW_MINUS, "25"),
        (Grade.REVIEW_MINUS, Grade.REVIEW_PLUS, "50"),
        (Grade.REVIEW_PLUS, Grade.EXPERT, "75"),
    ],
)
def test_exact_grade_thresholds_with_server_derived_evidence(grade: Grade, target: Grade, threshold: str) -> None:
    candidate = authority(uuid4(), uuid4(), value=threshold, grade=grade)
    evidence = evidence_for(candidate)
    gate = require_grade_entry(candidate, evidence, target)
    assert gate.target_grade == target
    assert len(gate.successful_case_ids) == 2
    assert len(gate.good_review_ids) == 3
    with pytest.raises(PolicyViolation):
        require_grade_entry(replace(candidate, value=Decimal(threshold) - Decimal("0.001")), evidence, target)


def test_duplicate_failed_pending_other_skill_and_other_user_cases_do_not_unlock_grade() -> None:
    candidate = authority(uuid4(), uuid4(), value="25", grade=Grade.PERFORMER, grant=Grant.NONE)
    evidence = evidence_for(candidate)
    first = evidence.successful_cases[0]
    for invalid in (
        first,
        replace(first, case_id=uuid4(), finalized=False),
        replace(first, case_id=uuid4(), approved=False),
        replace(first, case_id=uuid4(), solution_score=Decimal("69.999")),
        replace(first, case_id=uuid4(), skill_id=uuid4()),
        replace(first, case_id=uuid4(), participant_id=uuid4()),
    ):
        with pytest.raises(PolicyViolation):
            require_grade_entry(candidate, replace(evidence, successful_cases=(first, invalid)), Grade.REVIEW_MINUS)


def test_review_plus_requires_previous_probation_and_three_distinct_supervised_good_reviews() -> None:
    candidate = authority(uuid4(), uuid4(), value="50", grade=Grade.REVIEW_MINUS)
    evidence = evidence_for(candidate)
    first, second, third = evidence.quality_assessments
    invalid_evidence = (
        replace(evidence, probations=()),
        replace(evidence, quality_assessments=(first, second)),
        replace(evidence, quality_assessments=(first, second, second)),
        replace(
            evidence,
            quality_assessments=(first, second, replace(second, review=replace(second.review, revision_id=uuid4()))),
        ),
        replace(evidence, quality_assessments=(first, second, replace(third, decision=QualityDecision.NEEDS_REVISION))),
        replace(
            evidence,
            quality_assessments=(first, second, replace(third, review=replace(third.review, reviewer_id=uuid4()))),
        ),
        replace(
            evidence,
            quality_assessments=(first, second, replace(third, review=replace(third.review, probation_bindings=()))),
        ),
    )
    for invalid in invalid_evidence:
        with pytest.raises(PolicyViolation):
            require_grade_entry(candidate, invalid, Grade.REVIEW_PLUS)
    with pytest.raises(PolicyViolation):
        require_grade_entry(replace(candidate, grant=Grant.REVOKED), evidence, Grade.REVIEW_PLUS)
    with pytest.raises(PolicyViolation):
        require_grade_entry(replace(candidate, grade=Grade.PERFORMER), evidence, Grade.REVIEW_PLUS)


def test_new_nonexpert_grade_starts_new_empty_probation_without_granting_rights() -> None:
    candidate = authority(uuid4(), uuid4(), value="50", grade=Grade.REVIEW_MINUS)
    evidence = evidence_for(candidate)
    gate = require_grade_entry(candidate, evidence, Grade.REVIEW_PLUS)
    probation = start_probation(uuid4(), gate)
    assert probation.grade == Grade.REVIEW_PLUS
    assert not probation.passed
    assert not probation.confirmed_review_ids
    assert candidate.grade == Grade.REVIEW_MINUS
    with pytest.raises(PolicyViolation):
        start_probation(uuid4(), replace(gate, target_grade=Grade.EXPERT))
    with pytest.raises(PolicyViolation):
        start_probation(uuid4(), replace(gate, policy_version="unknown"))


def test_readiness_is_independent_per_skill_records_evidence_and_does_not_grant() -> None:
    candidate = authority(uuid4(), uuid4(), value="75", grade=Grade.REVIEW_PLUS)
    evidence = evidence_for(candidate)
    request = request_expert_readiness(uuid4(), candidate, evidence)
    assessor = authority(uuid4(), candidate.skill_id, value="90")
    result = assess_expert_readiness(
        request,
        candidate,
        evidence,
        assessor,
        decision=ReadinessDecision.APPROVED,
        feedback="Ready to mentor SQL reviewers.",
    )
    assert result.request.evidence == request.evidence
    assert result.current_evidence == request.evidence
    assert result.assessor.value == 90
    assert candidate.grade == Grade.REVIEW_PLUS
    assert not candidate.expert_confirmed
    field_name = "application_id"
    with pytest.raises(FrozenInstanceError):
        setattr(request, field_name, uuid4())


def test_readiness_revalidates_gates_and_current_expert_rights_at_decision() -> None:
    candidate = authority(uuid4(), uuid4(), value="75", grade=Grade.REVIEW_PLUS)
    evidence = evidence_for(candidate)
    request = request_expert_readiness(uuid4(), candidate, evidence)
    assessor = authority(uuid4(), candidate.skill_id)
    for invalid in (
        replace(assessor, user_id=candidate.user_id),
        replace(assessor, skill_id=uuid4()),
        replace(assessor, grade=Grade.REVIEW_PLUS),
        replace(assessor, expert_confirmed=False),
        replace(assessor, grant=Grant.REVOKED),
    ):
        with pytest.raises(PolicyViolation):
            assess_expert_readiness(
                request, candidate, evidence, invalid, decision=ReadinessDecision.APPROVED, feedback="Ready"
            )
    for changed in (
        replace(candidate, value=Decimal("74.999")),
        replace(candidate, grant=Grant.REVOKED),
        replace(candidate, user_id=uuid4()),
        replace(candidate, skill_id=uuid4()),
    ):
        with pytest.raises(PolicyViolation):
            assess_expert_readiness(
                request, changed, evidence, assessor, decision=ReadinessDecision.APPROVED, feedback="Ready"
            )
    for changed_evidence in (replace(evidence, probations=evidence.probations[:1]), GradeEvidenceInput()):
        with pytest.raises(PolicyViolation):
            assess_expert_readiness(
                request, candidate, changed_evidence, assessor, decision=ReadinessDecision.APPROVED, feedback="Ready"
            )
    rejection = assess_expert_readiness(
        request,
        candidate,
        GradeEvidenceInput(),
        assessor,
        decision=ReadinessDecision.REJECTED,
        feedback="More evidence needed",
    )
    assert rejection.current_evidence is None
    assert rejection.request.evidence == request.evidence


def test_readiness_needs_both_prior_probations_and_matching_request_feedback() -> None:
    candidate = authority(uuid4(), uuid4(), value="100", grade=Grade.REVIEW_PLUS)
    evidence = evidence_for(candidate)
    for prior in (evidence.probations[:1], evidence.probations[1:]):
        with pytest.raises(PolicyViolation):
            request_expert_readiness(uuid4(), candidate, replace(evidence, probations=prior))
    request = request_expert_readiness(uuid4(), candidate, evidence)
    assessor = authority(uuid4(), candidate.skill_id)
    with pytest.raises(PolicyViolation):
        assess_expert_readiness(
            request, candidate, evidence, assessor, decision=ReadinessDecision.APPROVED, feedback=" "
        )
    with pytest.raises(PolicyViolation):
        assess_expert_readiness(
            replace(request, evidence=replace(request.evidence, policy_version="unknown")),
            candidate,
            evidence,
            assessor,
            decision=ReadinessDecision.APPROVED,
            feedback="Ready",
        )


def test_policy_input_validation_and_historical_evidence_fail_closed() -> None:
    user, skill = uuid4(), uuid4()
    probation = probation_for(user, skill)
    review = review_for((probation,))
    with pytest.raises(PolicyViolation):
        replace(review, skill_ids=frozenset())
    with pytest.raises(PolicyViolation):
        replace(review, author_id=user)
    with pytest.raises(PolicyViolation):
        replace(review, probation_bindings=review.probation_bindings * 2)
    with pytest.raises(PolicyViolation):
        replace(probation, grade=Grade.EXPERT)
    with pytest.raises(PolicyViolation):
        replace(probation, confirmed_review_ids=frozenset(uuid4() for _ in range(4)))
    confirmation = confirmation_for(review)
    with pytest.raises(PolicyViolation):
        replace(confirmation, quality_score=Decimal("NaN"))
    with pytest.raises(PolicyViolation):
        replace(confirmation, quality_score=Decimal("69.999"))
    with pytest.raises(PolicyViolation):
        advance_probation(probation, review, replace(confirmation, policy_version="unknown"))
    with pytest.raises(PolicyViolation):
        advance_probation(probation, review, replace(confirmation, expert_id=user))
    with pytest.raises(PolicyViolation):
        advance_probation(
            probation,
            review,
            replace(confirmation, expert_id=user, expert_snapshots=(authority(user, skill),)),
        )
