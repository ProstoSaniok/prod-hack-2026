from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.application.project.entity import SubmissionId
from src.application.review.entity import (
    QualityAssessment,
    QualityAssessmentId,
    QualityDecision,
    ReviewAssignmentId,
    ReviewJobId,
    ReviewRevisionId,
)
from src.application.user.entity import UserId


def test_confirmed_quality_requires_seventy() -> None:
    with pytest.raises(ValueError, match="at least 70"):
        QualityAssessment(
            id=QualityAssessmentId(uuid4()),
            created_at=datetime.now(UTC),
            assignment_id=ReviewAssignmentId(uuid4()),
            job_id=ReviewJobId(uuid4()),
            submission_id=SubmissionId(uuid4()),
            revision_id=ReviewRevisionId(uuid4()),
            assessor_id=UserId(uuid4()),
            quality_score=Decimal("69.99"),
            decision=QualityDecision.CONFIRMED,
            feedback="Clarify the reasoning",
        )
