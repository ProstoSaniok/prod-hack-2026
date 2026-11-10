"""Only bounded metadata receipts may enter the durable request journal."""

from src.application.operations.idempotency import (
    ObjectShape,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)

APPLICATION_FIELDS = (
    "id",
    "user_id",
    "competency_id",
    "requested_grade",
    "user_competency_version",
    "status",
    "version",
    "created_at",
    "content_url",
)
ASSESSMENT_FIELDS = (
    "id",
    "application_id",
    "application_version",
    "applicant_id",
    "competency_id",
    "requested_grade",
    "expert_user_id",
    "expert_grant_id",
    "expert_grant_version",
    "expert_competency_version",
    "expert_value",
    "expert_grade",
    "expert_grant_state",
    "decision",
    "created_at",
    "content_url",
)


def capture_policy(path: str) -> ResponseCapturePolicy:
    names: tuple[str, ...]
    if path == "/api/v1/reviewer-applications":
        names = APPLICATION_FIELDS
    elif path.startswith("/api/v1/reviewer-applications/") and path.endswith("/assessments"):
        names = ASSESSMENT_FIELDS
    else:
        raise ValueError("Unregistered development mutation")
    return ResponseCapturePolicy(
        method="POST",
        path=path,
        allowed_statuses=frozenset({200, 201}),
        body_shape=ObjectShape(
            fields=tuple(
                ResponseField(
                    name=name, required=True, shape=ScalarShape(kinds=frozenset(ScalarKind), max_string_bytes=1024)
                )
                for name in names
            )
        ),
        location_prefix="/api/v1/reviewer-applications",
        max_body_bytes=65_536,
    )
