from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Connection

from tests.persistence.helpers import (
    Subject,
    insert,
)


def contribution_values(subject: Subject, review: UUID, revision: UUID, grant: UUID) -> dict[str, object]:
    return {
        "participant_id": subject.author,
        "case_id": subject.case,
        "competency_id": subject.competency,
        "review_id": review,
        "review_revision_id": revision,
        "source_id": uuid4(),
        "policy_version": "p01",
        "case_version": 1,
        "score": 80,
        "weight": Decimal("0.1"),
        "assessor_id": subject.expert,
        "assessor_grant_id": grant,
        "assessor_grant_version": 1,
        "assessor_competency_version": 1,
        "expertise_value": 75,
        "contribution": Decimal("9.200000000000000000000001"),
        "contribution_sum_before": Decimal("100.000000000000000000000009"),
        "contribution_sum_after": Decimal("109.200000000000000000000010"),
        "displayed_value_before": 100,
        "displayed_value_after": 100,
        "effective": True,
    }


def readiness_values(subject: Subject, application: UUID, expert_grant: UUID) -> dict[str, object]:
    return {
        "application_id": application,
        "application_version": 1,
        "applicant_id": subject.reviewer,
        "competency_id": subject.competency,
        "requested_grade": "EXPERT",
        "expert_user_id": subject.expert,
        "expert_grant_id": expert_grant,
        "expert_grant_version": 1,
        "expert_competency_version": 1,
        "expert_value": 90,
        "decision": "CONFIRMED",
        "feedback": "Independent readiness evidence",
        "expert_grade": "EXPERT",
        "expert_grant_state": "CONFIRMED",
    }


def seed_application(connection: Connection, subject: Subject) -> UUID:
    return insert(
        connection,
        "reviewer_applications",
        user_id=subject.reviewer,
        competency_id=subject.competency,
        requested_grade="EXPERT",
        user_competency_version=1,
        evidence="Prior reviewer gates are recorded",
        state="PENDING",
    )
