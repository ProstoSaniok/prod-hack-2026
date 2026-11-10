"""Exact opt-in replay policies for the small Git-backed HTTP surface."""

from src.application.operations.idempotency import (
    ArrayShape,
    ObjectShape,
    ResponseCapturePolicy,
    ResponseField,
    ScalarKind,
    ScalarShape,
)
from src.application.project.learning import LearningAction, LearningCommand


def mutation_route(command: LearningCommand) -> tuple[str, str]:
    action = command.action
    if action == LearningAction.START:
        return "POST", "/api/v1/me/projects"
    if action == LearningAction.SUBMIT:
        return "POST", f"/api/v1/projects/{command.target_id}/submissions"
    if action == LearningAction.PUBLISH:
        return "PATCH", f"/api/v1/admin/cases/{command.target_id}"
    if action.value.startswith("project."):
        root = f"/api/v1/projects/{command.target_id}"
    else:
        root = f"/api/v1/admin/cases/{command.target_id}"
        if action.value.startswith("reference."):
            root += "/reference-solution"
    if action == LearningAction.REFERENCE_INIT:
        return "PUT", root
    verb = action.value.split(".")[1]
    return (
        ("POST", root + "/files")
        if verb == "create"
        else ("PUT" if verb == "replace" else "DELETE", root + "/files/content")
    )


def capture_policy(command: LearningCommand) -> ResponseCapturePolicy:
    method, path = mutation_route(command)
    if command.action == LearningAction.SUBMIT:
        return ResponseCapturePolicy(
            method=method,
            path=path,
            allowed_statuses=frozenset({200, 201}),
            body_shape=_submission_shape(),
            location_prefix="/api/v1/submissions",
            max_body_bytes=262_144,
        )
    if command.action == LearningAction.PUBLISH:
        scalar = ScalarShape(kinds=frozenset(ScalarKind), max_string_bytes=65_536)
        shape = ObjectShape(
            fields=(
                *(
                    ResponseField(name=name, shape=scalar, required=True)
                    for name in (
                        "id",
                        "version",
                        "title",
                        "description",
                        "expected_result",
                        "difficulty",
                        "status",
                        "frozen",
                        "created_at",
                        "updated_at",
                    )
                ),
                ResponseField(
                    name="competencies",
                    required=True,
                    shape=ArrayShape(
                        max_items=100,
                        items=ObjectShape(
                            fields=(
                                *(
                                    ResponseField(name=name, shape=scalar, required=True)
                                    for name in ("competency_id", "required_value", "weight")
                                ),
                            )
                        ),
                    ),
                ),
            )
        )
        return ResponseCapturePolicy(
            method=method, path=path, allowed_statuses=frozenset({200}), body_shape=shape, max_body_bytes=262_144
        )
    fields = {
        LearningAction.START: frozenset({
            "id",
            "case_id",
            "case_version",
            "owner_id",
            "status",
            "started_at",
            "updated_at",
            "head_commit_sha",
            "working_branch",
            "repository",
            "upstream_repository",
            "submission_id",
        }),
        LearningAction.REFERENCE_INIT: frozenset({
            "id",
            "case_id",
            "version",
            "repository",
            "head_commit_sha",
            "ready",
            "ready_commit_sha",
            "created_at",
            "updated_at",
        }),
    }.get(command.action, frozenset({"path", "name", "type", "sha", "size", "commit_sha", "content"}))
    return ResponseCapturePolicy(
        method=method,
        path=path,
        allowed_statuses=frozenset({200, 201, 204}),
        allowed_json_fields=fields,
        allow_empty_body=method == "DELETE",
        location_prefix="/api/v1",
        max_body_bytes=65_536,
    )


def _submission_shape() -> ObjectShape:
    string = ScalarShape(kinds=frozenset({ScalarKind.STRING}), max_string_bytes=40_000)
    number = ScalarShape(kinds=frozenset({ScalarKind.NUMBER, ScalarKind.INTEGER}))
    integer = ScalarShape(kinds=frozenset({ScalarKind.INTEGER}))
    null = ScalarShape(kinds=frozenset({ScalarKind.NULL}))
    competency = ObjectShape(
        fields=(
            ResponseField(name="competency_id", shape=string, required=True),
            ResponseField(name="required_value", shape=number, required=True),
            ResponseField(name="weight", shape=number, required=True),
        )
    )
    author = ObjectShape(
        fields=(
            *(
                ResponseField(name=name, shape=string, required=True)
                for name in ("competency_id", "grade", "grant", "snapshotted_at")
            ),
            ResponseField(name="value", shape=number, required=True),
            ResponseField(
                name="expert_confirmed", shape=ScalarShape(kinds=frozenset({ScalarKind.BOOLEAN})), required=True
            ),
            ResponseField(name="evidence_ids", shape=ArrayShape(items=string, max_items=2), required=True),
        )
    )
    return ObjectShape(
        fields=(
            *(
                ResponseField(name=name, shape=string, required=True)
                for name in (
                    "id",
                    "project_id",
                    "case_id",
                    "author_id",
                    "status",
                    "submitted_at",
                    "commit_sha",
                    "base_commit_sha",
                    "retained_ref",
                    "base_retained_ref",
                )
            ),
            *(
                ResponseField(name=name, shape=integer, required=True)
                for name in ("case_version", "pull_request_id", "pull_request_number", "review_repository_id")
            ),
            *(
                ResponseField(name=name, shape=null, required=True)
                for name in ("effective_review_id", "pull_request_url", "repository")
            ),
            ResponseField(name="summary", shape=string),
            ResponseField(name="competencies", shape=ArrayShape(items=competency, max_items=100), required=True),
            ResponseField(name="author_skills", shape=ArrayShape(items=author, max_items=100), required=True),
        )
    )
