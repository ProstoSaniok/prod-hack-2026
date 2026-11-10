"""Bounded, private review responses explicitly permitted in the request journal."""

from src.application.operations.idempotency import (
    ArrayShape,
    ObjectShape,
    QueueStatePolicy,
    QueueStateRule,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)


def fields(*names: str) -> tuple[ResponseField, ...]:
    scalar = ScalarShape(kinds=frozenset(ScalarKind), max_string_bytes=24_000)
    return tuple(ResponseField(name=name, shape=scalar, required=True) for name in names)


def task_shape() -> ObjectShape:
    skill = ObjectShape(
        fields=(
            *fields("competency_id", "grade", "grant", "expert_confirmed", "snapshotted_at", "value"),
            ResponseField(
                name="evidence_ids",
                required=True,
                shape=ArrayShape(items=ScalarShape(kinds=frozenset({ScalarKind.STRING})), max_items=2),
            ),
        )
    )
    return ObjectShape(
        fields=(
            *fields(
                "id",
                "kind",
                "submission_id",
                "reviewer_id",
                "review_id",
                "revision_id",
                "recheck_id",
                "draft_id",
                "status",
                "assigned_at",
                "lease_expires_at",
            ),
            ResponseField(
                name="can_continue", required=False, shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN}))
            ),
            ResponseField(
                name="supervised_competency_ids",
                required=True,
                shape=ArrayShape(items=ScalarShape(kinds=frozenset({ScalarKind.STRING})), max_items=100),
            ),
            ResponseField(name="reviewer_skills", required=True, shape=ArrayShape(items=skill, max_items=100)),
        )
    )


def capture_policy(method: str, path: str) -> ResponseCapturePolicy:
    if method == "DELETE" and path.startswith("/api/v1/review-assignments/"):
        return ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({204}),
            body_shape=ObjectShape(fields=()),
            allow_empty_body=True,
            max_body_bytes=1024,
        )
    if (
        method == "POST"
        and path.startswith("/api/v1/reviews/")
        and path.endswith(("/quality-assessments", "/revisions"))
    ):
        names = (
            (
                "id",
                "review_id",
                "assignment_id",
                "revision_id",
                "assessor_id",
                "decision",
                "score",
                "created_at",
                "content_url",
            )
            if path.endswith("/quality-assessments")
            else ("id", "review_id", "number", "draft_version", "created_at", "content_url")
        )
        return ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({200, 201}),
            body_shape=ObjectShape(fields=fields(*names)),
            location_prefix="/api/v1/reviews",
            max_body_bytes=65_536,
        )
    if method == "POST" and path.startswith("/api/v1/review-assignments/") and path.endswith("/reviews"):
        return ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({200, 201}),
            body_shape=ObjectShape(
                fields=fields(
                    "id",
                    "assignment_id",
                    "submission_id",
                    "reviewer_id",
                    "status",
                    "revision_id",
                    "revision_number",
                    "draft_version",
                    "created_at",
                    "published_at",
                    "content_url",
                )
            ),
            location_prefix="/api/v1/reviews",
            max_body_bytes=65_536,
        )
    if method == "POST" and path == "/api/v1/me/review-assignments":
        return ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({201, 204}),
            body_shape=task_shape(),
            allow_empty_body=True,
            location_prefix="/api/v1/review-assignments",
            max_body_bytes=262_144,
            queue_state_policy=QueueStatePolicy(
                rules=(QueueStateRule(status_code=204, allowed_values=frozenset({"empty", "no_eligible"})),)
            ),
        )
    if method == "PUT" and path == "/api/v1/me/review-mode":
        shape = ObjectShape(
            fields=(
                *fields("enabled", "status"),
                ResponseField(
                    name="can_review", required=False, shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN}))
                ),
                ResponseField(
                    name="can_assess_readiness",
                    required=False,
                    shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN})),
                ),
                ResponseField(name="tasks", required=True, shape=ArrayShape(items=task_shape(), max_items=1)),
            )
        )
    elif method == "PUT" and path.startswith("/api/v1/review-assignments/") and path.endswith("/draft"):
        shape = ObjectShape(
            fields=(
                *fields(
                    "id", "assignment_id", "submission_id", "reviewer_id", "version", "verdict", "score", "saved_at"
                ),
                ResponseField(
                    name="feedback",
                    required=True,
                    shape=ObjectShape(fields=fields("strengths", "problems", "next_steps")),
                ),
                ResponseField(
                    name="comments",
                    required=True,
                    shape=ArrayShape(
                        max_items=20,
                        items=ObjectShape(
                            fields=(
                                *fields("body", "path", "line", "side"),
                                ResponseField(
                                    name="end_line",
                                    required=False,
                                    shape=ScalarShape(kinds=frozenset({ScalarKind.INTEGER, ScalarKind.NULL})),
                                ),
                            )
                        ),
                    ),
                ),
            )
        )
    else:
        raise ValueError("Unregistered review mutation")
    return ResponseCapturePolicy(
        method=method, path=path, allowed_statuses=frozenset({200}), body_shape=shape, max_body_bytes=524_288
    )
