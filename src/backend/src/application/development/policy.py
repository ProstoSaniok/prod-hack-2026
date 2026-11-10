"""Pure P01/P03/P04/P05 rules; callers own fresh reads, locking and the single UoW."""

from dataclasses import replace
from uuid import UUID

from src.application.development.policy_types import (
    POLICY_VERSION,
    CaseSkillInput,
    Grade,
    GradeEvidenceInput,
    GradeGateEvidence,
    Grant,
    PolicyViolationError,
    ProbationBinding,
    ProbationSnapshot,
    QualityAssessmentInput,
    QualityDecision,
    QualityEvidence,
    ReadinessAssessment,
    ReadinessDecision,
    ReadinessRequest,
    ReviewSnapshot,
    SkillAuthorityInput,
    validate_enum,
)

_GRADES = (Grade.PERFORMER, Grade.REVIEW_MINUS, Grade.REVIEW_PLUS, Grade.EXPERT)


def _authorities(user_id: UUID, rights: tuple[SkillAuthorityInput, ...]) -> dict[UUID, SkillAuthorityInput]:
    if any(right.user_id != user_id for right in rights) or len({right.skill_id for right in rights}) != len(rights):
        raise PolicyViolationError("Authority inputs must be unique per skill and belong to the actor")
    return {right.skill_id: right for right in rights}


def _require_expert(right: SkillAuthorityInput | None) -> SkillAuthorityInput:
    if (
        right is None
        or right.grade != Grade.EXPERT
        or right.grant != Grant.CONFIRMED
        or not right.expert_confirmed
        or right.value < 75
    ):
        raise PolicyViolationError("Current confirmed expert rights in the relevant skill are required")
    return right


def require_solution_reviewer(
    reviewer_id: UUID,
    author_id: UUID,
    requirements: tuple[CaseSkillInput, ...],
    rights: tuple[SkillAuthorityInput, ...],
) -> frozenset[UUID]:
    """Return skills requiring supervision; never allocate a queue job here."""
    if (
        reviewer_id == author_id
        or not requirements
        or len({item.skill_id for item in requirements}) != len(requirements)
    ):
        raise PolicyViolationError("Self-review, empty or duplicate requirements are forbidden")
    by_skill = _authorities(reviewer_id, rights)
    supervised = set[UUID]()
    for requirement in requirements:
        right = by_skill.get(requirement.skill_id)
        case_floor = 3 if requirement.required_value >= 75 else 2 if requirement.required_value >= 50 else 1
        author_floor = min(_GRADES.index(requirement.author_grade) + 1, 3)
        floor = max(case_floor, author_floor)
        if (
            right is None
            or right.grant not in {Grant.SUPERVISED, Grant.CONFIRMED}
            or right.value < requirement.required_value
            or _GRADES.index(right.grade) < floor
        ):
            raise PolicyViolationError("Reviewer does not meet every per-skill requirement")
        if right.grade == Grade.EXPERT:
            _require_expert(right)
        if right.grant == Grant.SUPERVISED:
            supervised.add(right.skill_id)
    return frozenset(supervised)


def requires_quality_confirmation(review: ReviewSnapshot) -> bool:
    """Use the assignment snapshot even if the reviewer graduates concurrently."""
    return bool(review.probation_bindings)


def assess_review_quality(
    review: ReviewSnapshot,
    assessment: QualityAssessmentInput,
    current_expert_rights: tuple[SkillAuthorityInput, ...],
) -> QualityEvidence:
    assignment = assessment.assignment
    if (
        not assignment.active
        or assignment.review_id != review.review_id
        or assignment.revision_id != review.revision_id
        or assignment.expert_id in {review.author_id, review.reviewer_id}
    ):
        raise PolicyViolationError("Inactive, stale or conflicted quality assignment")
    rights = _authorities(assignment.expert_id, current_expert_rights)
    snapshots = tuple(_require_expert(rights.get(skill_id)) for skill_id in sorted(review.skill_ids))
    if assessment.decision == QualityDecision.CONFIRMED and assessment.quality_score < 70:
        raise PolicyViolationError("Confirmation requires review quality of at least 70")
    return QualityEvidence(
        review=review,
        assignment_id=assignment.assignment_id,
        expert_id=assignment.expert_id,
        expert_snapshots=snapshots,
        decision=assessment.decision,
        quality_score=assessment.quality_score,
        feedback=assessment.feedback,
    )


def _good_confirmation(evidence: QualityEvidence) -> bool:
    # These are historical server-derived snapshots, not the expert's current rights.
    if evidence.policy_version != POLICY_VERSION:
        raise PolicyViolationError("Unsupported historical policy version; an explicit mapper is required")
    rights = _authorities(evidence.expert_id, evidence.expert_snapshots)
    if evidence.expert_id in {evidence.review.author_id, evidence.review.reviewer_id}:
        raise PolicyViolationError("Conflicted quality evidence")
    for skill_id in evidence.review.skill_ids:
        _require_expert(rights.get(skill_id))
    return evidence.decision == QualityDecision.CONFIRMED and evidence.quality_score >= 70


def advance_probation(
    probation: ProbationSnapshot,
    review: ReviewSnapshot,
    confirmation: QualityEvidence,
) -> ProbationSnapshot:
    """An immutable transition, not a concurrency primitive; count underlying reviews once."""
    binding = ProbationBinding(probation_id=probation.probation_id, skill_id=probation.skill_id, grade=probation.grade)
    if (
        confirmation.review != review
        or probation.user_id != review.reviewer_id
        or binding not in review.probation_bindings
    ):
        raise PolicyViolationError("Confirmation must match this revision and the snapshotted probation epoch")
    if not _good_confirmation(confirmation) or probation.passed or review.review_id in probation.confirmed_review_ids:
        return probation
    return replace(probation, confirmed_review_ids=probation.confirmed_review_ids | {review.review_id})


def require_grade_entry(
    candidate: SkillAuthorityInput, evidence: GradeEvidenceInput, target: Grade
) -> GradeGateEvidence:
    """Check sequential per-skill evidence without granting rights or accepting counters."""
    validate_enum(target, Grade)
    if target not in _GRADES[1:] or _GRADES.index(target) != _GRADES.index(candidate.grade) + 1:
        raise PolicyViolationError("Grade entry must follow the previous per-skill grade")
    cases = frozenset(
        item.case_id
        for item in evidence.successful_cases
        if item.participant_id == candidate.user_id
        and item.skill_id == candidate.skill_id
        and item.finalized
        and item.approved
        and item.solution_score >= 70
    )
    good_reviews = frozenset(
        item.review.review_id
        for item in evidence.quality_assessments
        if item.review.reviewer_id == candidate.user_id
        and candidate.skill_id in item.review.skill_ids
        and any(binding.skill_id == candidate.skill_id for binding in item.review.probation_bindings)
        and _good_confirmation(item)
    )
    probations = tuple(
        item
        for item in evidence.probations
        if item.user_id == candidate.user_id and item.skill_id == candidate.skill_id and item.passed
    )
    passed_grades = {item.grade for item in probations}
    threshold = {Grade.REVIEW_MINUS: 25, Grade.REVIEW_PLUS: 50, Grade.EXPERT: 75}[target]
    if candidate.value < threshold or len(cases) < 2:
        raise PolicyViolationError("Insufficient skill value or distinct successful cases")
    if target in {Grade.REVIEW_PLUS, Grade.EXPERT} and (
        candidate.grant != Grant.CONFIRMED or Grade.REVIEW_MINUS not in passed_grades or len(good_reviews) < 3
    ):
        raise PolicyViolationError("Previous probation and three distinct expert-confirmed reviews are required")
    if target == Grade.EXPERT and Grade.REVIEW_PLUS not in passed_grades:
        raise PolicyViolationError("The review-plus probation must pass before expert readiness")
    return GradeGateEvidence(
        user_id=candidate.user_id,
        skill_id=candidate.skill_id,
        target_grade=target,
        value=candidate.value,
        successful_case_ids=cases,
        good_review_ids=good_reviews,
        passed_probation_ids=frozenset(item.probation_id for item in probations),
    )


def start_probation(probation_id: UUID, gate: GradeGateEvidence) -> ProbationSnapshot:
    if gate.policy_version != POLICY_VERSION or gate.target_grade not in {Grade.REVIEW_MINUS, Grade.REVIEW_PLUS}:
        raise PolicyViolationError("A current non-expert grade-entry gate is required")
    return ProbationSnapshot(
        probation_id=probation_id, user_id=gate.user_id, skill_id=gate.skill_id, grade=gate.target_grade
    )


def request_expert_readiness(
    application_id: UUID, candidate: SkillAuthorityInput, evidence: GradeEvidenceInput
) -> ReadinessRequest:
    return ReadinessRequest(
        application_id=application_id, evidence=require_grade_entry(candidate, evidence, Grade.EXPERT)
    )


def assess_expert_readiness(
    request: ReadinessRequest,
    candidate: SkillAuthorityInput,
    evidence: GradeEvidenceInput,
    assessor: SkillAuthorityInput,
    *,
    decision: ReadinessDecision,
    feedback: str,
) -> ReadinessAssessment:
    validate_enum(decision, ReadinessDecision)
    if (
        request.evidence.policy_version != POLICY_VERSION
        or request.evidence.target_grade != Grade.EXPERT
        or candidate.user_id != request.evidence.user_id
        or candidate.skill_id != request.evidence.skill_id
        or assessor.user_id == candidate.user_id
        or assessor.skill_id != candidate.skill_id
        or not feedback.strip()
    ):
        raise PolicyViolationError("Readiness requires matching evidence, independent assessor and feedback")
    _require_expert(assessor)
    current = require_grade_entry(candidate, evidence, Grade.EXPERT) if decision == ReadinessDecision.APPROVED else None
    return ReadinessAssessment(
        request=request, assessor=assessor, decision=decision, feedback=feedback, current_evidence=current
    )
