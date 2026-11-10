"""Validate the candidate, not the running backend or Gitea ACLs.

Run with openapi-spec-validator==0.7.2 in an isolated tool environment (see
CONTRACT.md). External references are forbidden, so validation never fetches URLs.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
METHODS = {"get", "post", "put", "patch", "delete"}
ORIGINAL_HASH = "9f93aee50e2c547c6800c4f56bdb23834e1b99d2594bf8099143ccf11c928754"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def walk(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from walk(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, (*path, index))


def resolve(doc, reference):
    require(reference.startswith("#/"), "External references forbidden")
    value = doc
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def deref(doc, value):
    return resolve(doc, value["$ref"]) if "$ref" in value else value


def expand_schema(doc, value, seen=()):
    """Inline this acyclic contract's local refs; no remote I/O or deprecated resolver."""
    if isinstance(value, dict):
        if "$ref" in value:
            reference = value["$ref"]
            require(reference not in seen, "Recursive example schema not supported")
            return expand_schema(doc, resolve(doc, reference), (*seen, reference))
        return {key: expand_schema(doc, item, seen) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_schema(doc, item, seen) for item in value]
    return value


def operations(doc):
    return {
        (path, method): operation
        for path, item in doc["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    }


def original_access_rule(path, method):
    """Independent route-level oracle for the audited v0.7 visibility boundaries."""
    if path in {"/healthcheck", "/ready"}:
        return "public-status"
    if path in {"/api/v1/competencies", "/api/v1/development/policy"}:
        return "authenticated-policy"
    if path == "/api/v1/cases" or path.startswith("/api/v1/cases/"):
        return "published-case-only"
    if path == "/api/v1/admin/candidates":
        return "consented-organizer-only"
    if path == "/api/v1/admin/cases" and method == "post":
        return "organizer-create-case"
    if path == "/api/v1/admin/cases" or path.startswith("/api/v1/admin/cases/"):
        return "case-owner-organizer-only"
    if path == "/api/v1/me/projects" and method == "post":
        return "participant-create-project"
    if path == "/api/v1/me/review-assignments" and method == "post":
        return "claim-own-assignment"
    if path.startswith("/api/v1/me/"):
        return "current-actor-only"
    if path.startswith("/api/v1/projects/"):
        return "project-owner-only"
    if path.startswith("/api/v1/submissions/"):
        return {
            "{submission_id}": "submission-owner-only",
            "snapshot": "pinned-submission-read",
            "feedback": "published-feedback-read",
            "progression": "published-progression-read",
            "recheck-requests": "submission-author-recheck",
        }[path.rsplit("/", 1)[-1]]
    if path.startswith("/api/v1/reviews/"):
        if path.endswith("/quality-assessments"):
            return "assigned-quality-expert" if method == "post" else "private-quality-read"
        return "review-resource-read"
    if path.startswith("/api/v1/review-assignments/"):
        return {
            "{assignment_id}": "assigned-reviewer-only",
            "draft": "assigned-reviewer-only",
            "workspace": "assigned-review-workspace",
            "reviews": "assigned-review-completion",
            "source-review": "assigned-source-review",
        }[path.rsplit("/", 1)[-1]]
    if path.startswith("/api/v1/recheck-requests/"):
        return "assigned-recheck-reviewer" if method == "post" else "recheck-resource-read"
    raise ValueError(f"Original operation lacks audited access rule: {method} {path}")


def check_structure(doc, original):
    status = doc["x-contract-status"]
    require(doc["info"]["version"] == "0.8.0-candidate.5", "Expected additive candidate.5")
    require(status["state"] == "candidate-not-frozen", "Candidate must not be marked frozen")
    require(
        status["isolation_proven"] is False and status["downstream_released"] is False,
        "Local observations are not accepted isolation proof; downstream release remains pending",
    )
    require(
        status["isolation_proven_scope"] == "complete-matrix-coordinator-accepted"
        and status["independent_review"] == "pending"
        and status["coordinator_acceptance"] == "pending"
        and status["component_gate"] == "held"
        and status["full_gate"] == "held"
        and status["gate_owner"] == "coordinator",
        "Local evidence must not imply coordinator acceptance",
    )
    require(
        status["approved_release"] is None and status["approved_image"] is None,
        "Record the tested local release separately from coordinator approval",
    )
    local = status["local_evidence"]
    require(
        local["state"] == "owner-run-reviewed-corrections-local-bounded-observations"
        and local["owner_authorized"] is True
        and local["environment"] == "disposable-local-gitea-sqlite3"
        and local["release"] == "28.1.0"
        and local["image"]
        == "docker.gitea.com/gitea@sha256:f505a0d3a41323b786e05942fc7a4075e07ae7c169fd9485649aa55ee543a21a",
        "Preserve the owner-authorized local SQLite release/digest evidence scope",
    )
    require(
        local["local_read_write_matrix_complete"] is True
        and local["latest_fresh_complete"] is True
        and local["historical_reported_rows"] == 66
        and local["latest_corrected_evidence"] == "owner-run-existing-named-matrix-passed-66-of-66"
        and local["run_id"] == "prodguild-t01-lab-bf59da5559"
        and local["live_operator"] == "sole-lead-owner"
        and local["corrected_rows"] == {"total": 66, "passed": 66, "failed": 0}
        and local["corrected_policies"]
        == {
            "marker_scan": "prodguild-t01-actor-forbidden-body-canaries-v2",
            "native_denial": "prodguild-t01-gitea28.1.0-exact-native-denial-v1",
        }
        and local["independent_review"]
        == {
            "scope": "corrections-only",
            "full_matrix_accepted": False,
            "reviewer_operated_live": False,
        }
        and local["dispute"] == "scanner-and-classifier"
        and local["dispute_scope"] == "historical-artifact-only"
        and local["new_live_proof"] is True
        and local["historical_evidence_path"] == "tests/integration/gitea-spike/evidence/local-matrix.json"
        and local["latest_corrected_evidence_path"]
        == "tests/integration/gitea-spike/evidence/local-matrix-correction.json"
        and local["evidence_scope"] == "actual-named-observations-only"
        and {
            "authenticated-browser-login-and-account-authorized-links",
            "file-blob-concurrency-lock-ref-403-and-stale-sha-422",
        }
        <= set(local["historical_observations"]),
        "Fresh sole-owner corrected66/66 is bounded named local evidence; historical dispute and full acceptance gates remain separate",
    )
    require(
        status["production_gates"]
        == {"mirror_approved": False, "postgresql_validated": False, "vm_deployment_approved": False},
        "Local SQLite evidence is not production mirror/PostgreSQL/VM approval",
    )
    boundaries = doc["x-git-boundaries"]
    require(
        boundaries["topology"] == "private-per-project-same-repo-pr"
        and boundaries["shared_upstream_private_fork_prs"] == "rejected-peer-diff-leak"
        and boundaries["reference_and_draft_repositories"] == "separate-private",
        "Keep isolated same-repo PRs; shared-upstream private forks leak peer diffs",
    )
    require(boundaries["authoritative_store"] == "PostgreSQL", "Git must not become business authority")
    require(
        boundaries["links"]
        == {
            "credential_free": True,
            "viewer_account_acl_required": True,
            "authorized_read_collaborator_pr_links": True,
            "nullable_pr_reasons": ["unavailable", "account-unprovisioned", "acl-unavailable"],
            "incidental_annotations_block_pr_links": False,
        },
        "Authorized PR links remain useful despite incidental annotations; null is for availability/account/ACL",
    )
    require(
        boundaries["projection"]
        == {
            "writer": "backend-service-account",
            "source": "committed-published-review-revision",
            "learner_can_edit_or_delete_canonical_projection": False,
            "pending_drafts": "PostgreSQL-only",
            "pending_quality_feedback": "PostgreSQL-only",
            "restricted_quality_results": "PostgreSQL-only",
            "comment_metadata_authority": "PostgreSQL",
        },
        "Only backend projects published revisions; pending/restricted content and canonical metadata stay in DB",
    )
    require(
        boundaries["incidental_annotations"] == {"trust": "untrusted", "business_effects": False},
        "Incidental annotations never determine grades, grants, confirmations or rewards",
    )
    compatibility = doc["x-gitea-compatibility"]
    require(
        compatibility["write_error_classification"] == "operation-and-verified-error-semantics-not-status-alone"
        and compatibility["recognized_write_conflicts"]
        == [
            {"observation": "concurrent-lock-ref", "gitea_status": 403, "api_status": 409, "api_code": "CONFLICT"},
            {"observation": "stale-blob-sha", "gitea_status": 422, "api_status": 409, "api_code": "STALE_BLOB"},
        ],
        "Recognized upstream403 lock-ref/422 stale-SHA conflicts map to409; status alone is not ACL classification",
    )
    require(
        compatibility["application_pinned_diff_validation_required"] is True
        and compatibility["upstream_comment_acceptance_proves_valid_position"] is False
        and compatibility["file_comment_projection"] == "safe-general-comment-with-authoritative-db-metadata",
        "Application must validate pinned diff positions; native acceptance cannot replace validation or fallback",
    )
    refs = 0
    for _, node in walk(doc):
        require("externalValue" not in node, "External examples forbidden, including unused components")
        if "$ref" in node:
            resolve(doc, node["$ref"])
            refs += 1
    ops = operations(doc)
    access_rules = doc["x-operation-access"]
    expected_rules = {original_access_rule(*key) for key in operations(original)}
    require(set(access_rules) == expected_rules, "Every original operation requires an audited access scope")
    for rule, clauses in {
        "published-case-only": ["published cases only", "Draft/archived", "404", "No reference contents/URLs"],
        "case-owner-organizer-only": [
            "Only the case-owner organizer",
            "own cases",
            "private reference contents/URLs",
            "no ownership bypass",
        ],
        "assigned-reviewer-only": [
            "Only the assigned reviewer",
            "read/replace its draft",
            "organizers cannot read it",
            "404",
        ],
        "assigned-review-workspace": ["Only the assigned reviewer of kind=review", "ASSIGNMENT_KIND_MISMATCH"],
        "assigned-source-review": [
            "Only the assigned relevant expert of kind=quality_check",
            "assignment-pinned",
            "T15-disabled",
        ],
        "project-owner-only": ["Only the project owner", "never this mutable working tree", "PROJECT_SUBMITTED"],
        "published-progression-read": ["submission author", "case-owner organizer", "before finalization return404"],
        "private-quality-read": [
            "Only the original reviewer",
            "after solution-review publication",
            "organizer by role alone have no access",
        ],
    }.items():
        require(all(clause in access_rules[rule] for clause in clauses), f"Access invariant weakened: {rule}")
    ids = [op["operationId"] for op in ops.values()]
    require(len(ids) == len(set(ids)), "Duplicate operation IDs")
    for key, old in operations(original).items():
        require(key in ops, f"Original operation removed: {key}")
        require(ops[key]["operationId"] == old["operationId"], f"Original operation ID changed: {key}")
        require(ops[key]["x-original-operation"] is True, "Missing original operation marker")
        expected_access = original_access_rule(*key)
        require(ops[key]["x-access-rule"] == expected_access, f"Original access boundary changed: {key}")
        require(
            "Access: " + access_rules[expected_access] in ops[key]["description"],
            f"Operation-specific visibility was replaced by blanket policy: {key}",
        )
        deferred = "recheck" in key[0] or key[0] == "/api/v1/admin/candidates"
        if deferred:
            require(
                ops[key]["x-delivery-phase"].endswith("disabled") and "501" in ops[key]["responses"],
                "Deferred original operation silently enabled",
            )
        else:
            require(ops[key]["x-delivery-phase"] == "mvp", "Original MVP operation silently deferred")
    for (path, method), op in ops.items():
        require(op["x-side-effects"] is (method != "get"), f"GET side effect / missing declaration: {path}")
        if method != "get" and path.startswith("/api/v1"):
            parameters = [deref(doc, p) for p in doc["paths"][path].get("parameters", []) + op.get("parameters", [])]
            require(
                any(p["name"] == "Idempotency-Key" and p["required"] for p in parameters),
                f"No required request key: {path}",
            )
        for code, raw in op["responses"].items():
            result = deref(doc, raw)
            if code == "201":
                require("Location" in result.get("headers", {}), f"Missing creation Location: {path}")
            if code == "204":
                require("content" not in result, f"204 must not contain body: {path}")
    s = doc["components"]["schemas"]
    for path, keys, policy, schema, request_schema in (
        ("/api/v1/me/projects", ["participant_id", "case_id"], "project_uniqueness", "Project", "CreateProjectRequest"),
        (
            "/api/v1/projects/{project_id}/submissions",
            ["project_id"],
            "submission_uniqueness",
            "Submission",
            "SubmitProjectRequest",
        ),
    ):
        op = ops[(path, "post")]
        require(
            op["x-business-deduplication"]
            == {
                "unique_by": keys,
                "lifetime": "permanent",
                "new_or_expired_request_key": "return-unchanged-existing-200",
                "duplicate_git_resources": False,
                "policy": policy,
            }
            and s[schema]["x-business-key"] == keys,
            f"Permanent business-key deduplication lost: {path}",
        )
        rule = doc["x-business-decisions"][policy]
        require(
            rule in op["description"]
            and rule == op["responses"]["200"]["description"]
            and rule in s[schema]["description"]
            and rule == s[request_schema]["description"],
            f"Existing200 semantics must remain explicit in operation/response/schemas: {path}",
        )
        require(
            op["responses"]["200"]["headers"]["Location"] == op["responses"]["201"]["headers"]["Location"]
            and op["responses"]["200"]["content"] == op["responses"]["201"]["content"],
            "Existing200 returns the same resource type and canonical Location",
        )
    submission_rule = doc["x-business-decisions"]["submission_uniqueness"]
    require(
        all(
            clause in submission_rule
            for clause in (
                "One Submission per project_id",
                "new or expired request key",
                "unchanged repeat",
                "PROJECT_SUBMITTED",
                "IDEMPOTENCY_CONFLICT",
                "OPERATION_IN_PROGRESS",
                "COMMIT_CHANGED",
            )
        ),
        "Submission uniqueness, unchanged-existing200 and conflict semantics must be explicit",
    )
    require(
        {
            "line comments require a changed file in the exact pinned diff",
            "file-level path must exist in the retained base/head tree",
            "line and side map to exact pinned diff",
            "upstream acceptance is not position validation",
        }
        <= set(s["ReviewComment"]["x-semantic-validation"]),
        "Review comments require application-side changed-path/pinned-diff validation",
    )
    for name, field in (
        ("Submission", "author_skills"),
        ("ReviewRevision", "reviewer_skills"),
        ("ReviewTask", "reviewer_skills"),
        ("ReviewQualityAssessment", "assessor_skills"),
    ):
        require(field in s[name]["required"], f"Mandatory per-skill snapshot not required: {name}.{field}")
        shape = s[name]["properties"][field]
        require(
            shape["type"] == "array"
            and shape["minItems"] >= 1
            and shape["items"] == {"$ref": "#/components/schemas/SkillSnapshot"}
            and shape["x-unique-by"] == "competency_id",
            f"Snapshot schema relationship changed: {name}.{field}",
        )
    require(
        s["ReviewResult"]["properties"]["current_revision"] == {"$ref": "#/components/schemas/ReviewRevision"}
        and "current_revision" in s["ReviewResult"]["required"],
        "ReviewResult must expose its exact immutable revision",
    )
    require(
        {"competency_id", "value", "grade", "grant", "expert_confirmed", "evidence_ids", "snapshotted_at"}
        <= set(s["SkillSnapshot"]["required"]),
        "SkillSnapshot evidence/grants missing",
    )
    require(
        {"base_commit_sha", "commit_sha", "base_retained_ref", "retained_ref"} <= set(s["Submission"]["required"]),
        "Both diff sides must be retained",
    )
    skills = s["CaseCompetency"]
    require(
        set(skills["required"]) == {"competency_id", "required_value", "weight"},
        "Case qualification/weight fields required",
    )
    require(
        skills["properties"]["required_value"] == {"type": "number", "minimum": 0, "maximum": 100},
        "Required value bounds",
    )
    require(
        skills["properties"]["weight"]["maximum"] == 0.25
        and skills["properties"]["weight"]["exclusiveMinimum"] is True,
        "Weight bounds",
    )
    for name in ("PublicCase", "AdminCase", "CreateCaseRequest", "UpdateCaseRequest", "Submission"):
        require(
            s[name]["properties"]["competencies"]["x-unique-by"] == "competency_id",
            f"Duplicate skill IDs not addressed: {name}",
        )
    for name, forbidden in {
        "Submission": ["author_grade"],
        "ReviewResult": ["reviewer_grade"],
        "ReviewQualityAssessment": ["assessor_grade"],
        "ProgressionResult": ["grade_before", "grade_after"],
    }.items():
        require(not set(forbidden) & s[name]["properties"].keys(), "Global-grade authority must not survive")
    require("review_id" not in s["ReviewDraft"]["properties"], "Draft is not a provisional review result")
    require({"id", "assignment_id", "version"} <= set(s["ReviewDraft"]["required"]), "Draft identity/version absent")
    require(
        {"assignment_id", "revision_id"} <= set(s["ReviewQualityAssessmentRequest"]["required"]),
        "Quality target must be exact",
    )
    require(
        s["CreateCaseRequest"]["properties"]["status"]["enum"] == ["draft"],
        "Published creation contradicts reference prerequisite",
    )
    require(s["DevelopmentPolicy"]["properties"]["state"]["enum"] == ["configured"], "MVP policy cannot be TBD")
    require(s["RepositoryLinks"]["properties"]["ssh_url"]["nullable"] is True, "Do not invent SSH")
    require(
        s["Submission"]["properties"]["pull_request_url"]["nullable"] is True,
        "PR links must permit unavailable/unprovisioned account ACLs",
    )
    require(s["Profile"]["properties"]["karma"].get("nullable", False) is False, "Real karma required")
    require(s["Profile"]["properties"]["prodits"].get("nullable", False) is False, "Real wallet required")
    expected_policy = {
        "expert_required_value": 75,
        "review_plus_required_value": 50,
        "grade_values": {"performer": 0, "review_minus": 25, "review_plus": 50, "expert": 75},
        "probation_successes": 3,
        "success_score": 70,
        "quality_score": 70,
        "max_active_assignments": 1,
        "assignment_lease_hours": 24,
        "priority_cost": 5,
        "priority_burst": 3,
        "ordinary_aging_hours": 24,
        "welcome_reward": 10,
        "case_reward": 3,
        "review_reward": 5,
        "review_karma": 1,
        "expertise_bonus_max": 0.2,
        "display_cap": 100,
    }
    require(doc["x-policy-values"] == expected_policy, "Agreed numeric policies changed")
    require(
        s["Health"]["example"] == {"ok": True} and set(s["Health"]["properties"]) == {"ok"},
        "Preserve actual health response, not v0.7 status",
    )
    for path, method, success in [
        ("/auth/sign_up/email", "post", "200"),
        ("/auth/sign_in/email", "post", "200"),
        ("/auth/me", "get", "200"),
        ("/auth/sign_out", "post", "204"),
    ]:
        require(success in ops[(path, method)]["responses"], "Legacy auth success status changed")
        for code in ("401", "409", "422"):
            if code in ops[(path, method)]["responses"]:
                result = ops[(path, method)]["responses"][code]
                expected = "LegacyValidationError" if code == "422" else "LegacyError"
                require(
                    result["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{expected}"},
                    "Legacy auth envelope changed",
                )
                if code == "401":
                    require(
                        result["headers"]["WWW-Authenticate"]["schema"]["enum"] == ["Bearer"],
                        "Legacy bearer challenge lost",
                    )
    for path in (
        "/api/v1/me/wallet",
        "/api/v1/me/wallet/transactions",
        "/api/v1/me/priority-requests",
        "/api/v1/reviewer-applications",
        "/api/v1/me/reviewer-applications",
        "/api/v1/reviews/{review_id}/revisions",
        "/api/v1/submissions/{submission_id}/files",
        "/api/v1/submissions/{submission_id}/files/content",
        "/api/v1/submissions/{submission_id}/diff",
        "/api/v1/submissions/{submission_id}/diff/lines",
        "/api/v1/reviewer-applications/{application_id}/assessments/{assessment_id}",
    ):
        require(path in doc["paths"], f"Required resource absent: {path}")
    for workspace in ("ReviewWorkspace", "AssignedReviewContent"):
        require(
            s[workspace]["properties"]["snapshot"]["nullable"] is True
            and {"snapshot_state", "files_url", "diff_url"} <= set(s[workspace]["required"]),
            "Large workspaces, including expert source-review, need immutable paginated fallback",
        )
    require("latest_assessment" in s["ReviewerApplication"]["required"], "Applicant must retrieve readiness feedback")
    for path, target in {
        "/api/v1/reviews/{review_id}/quality-assessments": "/api/v1/reviews/{review_id}/quality-assessments/{assessment_id}",
        "/api/v1/reviews/{review_id}/revisions": "/api/v1/reviews/{review_id}/revisions/{revision_id}",
        "/api/v1/reviewer-applications/{application_id}/assessments": "/api/v1/reviewer-applications/{application_id}/assessments/{assessment_id}",
    }.items():
        require("get" in doc["paths"][target], "Created resource must be retrievable")
        for code in ("200", "201"):
            require(
                ops[(path, "post")]["responses"][code]["headers"]["Location"]["example"] == target,
                "Location must identify created assessment/revision, not collection",
            )
    check_candidate_corrections(doc)
    return {
        "operations": len(ops),
        "original_operations": len(operations(original)),
        "original_access_rules_checked": len(operations(original)),
        "local_refs": refs,
    }


def check_candidate_corrections(doc):
    """Independent amendment invariants across parameters, responses, schemas and access prose."""
    by_id = {op["operationId"]: (path, op) for (path, _), op in operations(doc).items()}
    policies = doc["x-business-decisions"]
    schemas = doc["components"]["schemas"]
    params = doc["components"]["parameters"]
    require(
        params["IdempotencyKey"]["schema"] == {"type": "string", "minLength": 1, "maxLength": 128},
        "Public request-key schema remains length-only pending coordination",
    )
    boundary = doc["x-prodguild-integration-boundary"]
    require(
        boundary["state"] == "recommendations-pending-coordinator-signoff"
        and boundary["implemented_adapter"] is False
        and boundary["idempotency_key"]
        == {
            "public_schema": "length-only-1..128",
            "proposed_for_t03_confirmation": "^[A-Za-z0-9._~-]{1,128}$",
            "public_adoption": "pending-coordinator-confirmation",
        },
        "T03 recommendation must not imply a shipped adapter/public pattern",
    )
    require(
        boundary["file_mutation_response"]
        == {
            "state": "proposal-pending-coordinator-signoff",
            "mapper_implemented": False,
            "text_content": None,
            "serialized_success_json_max_bytes": 65536,
            "source_text_max_bytes": 1048576,
            "t03_serialized_response_max_bytes": 1048576,
            "t03_followup_commit": "1055b0e",
            "t03_branch_tip_at_handoff": "a71cc68",
        },
        "File mutation proposal must distinguish serialized envelope cap from source limit",
    )
    require(
        boundary["live_evidence"]
        == {
            "historical_path": "tests/integration/gitea-spike/evidence/local-matrix.json",
            "latest_corrected_evidence_path": "tests/integration/gitea-spike/evidence/local-matrix-correction.json",
            "fresh_run": "owner-run-reviewed-corrections-local-bounded-observations",
            "run_id": "prodguild-t01-lab-bf59da5559",
            "latest_fresh_complete": True,
            "live_operator": "sole-lead-owner",
            "rows": {"total": 66, "passed": 66, "failed": 0},
            "scope": "actual-named-observations-only",
            "corrected_policies": doc["x-contract-status"]["local_evidence"]["corrected_policies"],
            "independent_review": doc["x-contract-status"]["local_evidence"]["independent_review"],
            "failed_attempts": [
                {
                    "path": "tests/integration/gitea-spike/evidence/local-matrix-correction-attempt-01.json",
                    "passed": 44,
                    "total": 66,
                },
                {
                    "path": "tests/integration/gitea-spike/evidence/local-matrix-correction-attempt-02.json",
                    "passed": 65,
                    "total": 66,
                },
            ],
            "historical_rows_revalidated_by_old_labels": False,
        },
        "Corrected evidence is the separate sole-owner66/66 run, with scoped corrections review; old labels are not new proof",
    )
    review_capture = boundary["review_response_capture"]
    affected_review_ops = {
        "get_api_v1_submissions_submission_id_feedback",
        "get_api_v1_me_reviews",
        "get_review_workspace",
        "get_review_assignment_draft",
        "replace_review_assignment_draft",
        "create_review_result",
        "get_review_result",
        "create_recheck_review",
        "get_assigned_source_review",
        "create_review_revision",
        "get_review_revision",
    }
    require(
        review_capture
        == {
            "state": "blocked-pending-coordinated-design",
            "binding_gate": "held",
            "compatible_with_t03": False,
            "mapper_implemented": False,
            "t03_serialized_response_max_bytes": 1048576,
            "existing_review_schema_bounds": "unchanged-200-comments-times-10000-characters",
            "affected_operation_ids": review_capture["affected_operation_ids"],
            "preferred_proposal": "metadata-only-mutations-and-separately-authorized-versioned-content-get",
            "proposed_mutation_json_max_bytes": 65536,
            "proposed_read_page_json_max_bytes": 262144,
            "proposal_accepted": False,
        }
        and set(review_capture["affected_operation_ids"]) == affected_review_ops,
        "Oversized review capture binding must remain blocked with unchanged T03 ceiling and pending design",
    )
    capture_rule = policies["review_response_capture"]
    for clause in (
        "BLOCKER",
        "NOT compatible",
        "Binding remains held",
        "No truncation, replay-only redaction",
        "separately authorized GET",
        "not implemented",
    ):
        require(clause in capture_rule, f"Review capture blocker/proposal missing: {clause}")
    for operation_id in affected_review_ops:
        op = by_id[operation_id][1]
        require(
            op["x-prodguild-integration-gate"] == "review-response-capture-held" and capture_rule in op["description"],
            f"Potentially oversized review resource cannot silently bind: {operation_id}",
        )
    for name in (
        "SaveReviewDraftRequest",
        "ReviewDraft",
        "ReviewRevision",
        "ReviewResult",
        "ReviewWorkspace",
        "AssignedReviewContent",
    ):
        require(
            schemas[name]["x-prodguild-integration-gate"] == "review-response-capture-held"
            and capture_rule in schemas[name]["description"],
            "Review schema must expose held capture gate",
        )
    for name in ("SaveReviewDraftRequest", "ReviewDraft", "ReviewRevision"):
        require(
            schemas[name]["properties"]["comments"]["maxItems"] == 200,
            "Do not silently shrink review bounds to hide blocker",
        )
    require(
        schemas["ReviewComment"]["properties"]["body"]["maxLength"] == 10000,
        "Review comment bound must remain explicit",
    )
    require(
        boundary["wallet_history"]
        == {
            "state": "mapper-proposal-pending-t11-coordinator-signoff",
            "mapper_implemented": False,
            "sequence_scope": "wallet-owner",
            "order": "sequence-desc",
        },
        "Wallet sequence is an explicit pending owner-scoped mapper proposal",
    )
    ledger = by_id["list_my_wallet_transactions"][1]
    ledger_rule = policies["wallet_history"]
    require(
        ledger["x-ordering"] == "owner-sequence-desc"
        and ledger_rule in ledger["description"]
        and ledger["responses"]["200"]["description"] == ledger_rule
        and "collections sort created_at" not in ledger["description"]
        and ledger_rule in schemas["WalletTransaction"]["description"]
        and schemas["WalletTransactions"]["description"] == ledger_rule,
        "Ledger response/schema/prose must use owner-local sequence DESC, never generic timestamp ordering",
    )
    for clause in (
        "Only the current owner's",
        "ALL transaction kinds",
        "sequence DESC before limit/offset",
        "unique per wallet owner",
        "Foreign rows are never included",
    ):
        require(clause in ledger_rule, f"Ledger ownership/history ordering rule lost: {clause}")
    wallet_tx = schemas["WalletTransaction"]
    require(
        "sequence" in wallet_tx["required"]
        and wallet_tx["properties"]["sequence"]["type"] == "integer"
        and wallet_tx["properties"]["sequence"]["minimum"] == 1
        and wallet_tx["x-sequence-scope"] == "wallet-owner"
        and wallet_tx["x-sequence-immutable"] is True
        and wallet_tx["x-sequence-unique-per-owner"] is True,
        "Ledger sequence must be required, positive, immutable and owner-unique",
    )
    require(
        ledger["parameters"] == [{"$ref": "#/components/parameters/Limit"}, {"$ref": "#/components/parameters/Offset"}],
        "Preserve ledger pagination parameters",
    )
    ledger_rows = ledger["responses"]["200"]["content"]["application/json"]["example"]
    check_ledger_sequence_example(ledger_rows)
    require(
        {row["kind"] for row in ledger_rows} == set(wallet_tx["properties"]["kind"]["enum"]),
        "History example must include every ledger kind",
    )
    for (path, method), op in operations(doc).items():
        if "/files" not in path or method not in {"post", "put", "delete"}:
            continue
        require(
            op["x-prodguild-response-boundary"]
            == {
                "state": "proposal-pending-coordinator-signoff",
                "serialized_success_json_max_bytes": 65536,
                "metadata_only": True,
                "exact_first_replay_body": True,
            },
            f"Mutation response boundary lost: {op['operationId']}",
        )
        rule = policies["file_mutation_response"]
        require(rule in op["description"], "Mutation response proposal missing from operation")
        for code in ("200", "201", "204"):
            if code not in op["responses"]:
                continue
            result = op["responses"][code]
            require(rule in result["description"], "Mutation response cap/body rule missing")
            if code != "204":
                shape = result["content"]["application/json"]["schema"]
                binary = "reference-solution" not in path and "/admin/cases/" in path
                expected = (
                    {"$ref": "#/components/schemas/BinaryFileMetadata"}
                    if binary
                    else {
                        "allOf": [
                            {"$ref": "#/components/schemas/ProjectFile"},
                            {
                                "type": "object",
                                "properties": {"content": {"type": "string", "nullable": True, "enum": [None]}},
                            },
                        ],
                    }
                )
                require(shape == expected, "Text mutation must enforce content=null; binary metadata remains metadata")
        if "201" in op["responses"]:
            require(
                op["responses"]["200"]["content"] == op["responses"]["201"]["content"],
                "First/replay mutation body shape mismatch",
            )
    file_path = params["FilePath"]
    directory = params["DirectoryPath"]
    require(file_path["required"] is True and file_path["schema"]["minLength"] == 1, "Content path required")
    require(
        directory["name"] == "path"
        and directory["in"] == "query"
        and directory["required"] is False
        and directory["schema"]["default"] == ""
        and directory["schema"]["minLength"] == 0
        and directory["schema"]["maxLength"] == file_path["schema"]["maxLength"] == 1024,
        "Directory omitted/empty=root with the same upper path bound",
    )
    for clause in (
        "1024 UTF-8 bytes",
        "exactly one transport percent-decoding",
        "absolute paths",
        "empty/dot/dot-dot components",
        "backslash",
        "NUL",
        "case-insensitive .git",
        "symlinks and submodules in any traversed component",
        "Do not decode again",
        "Remaining percent sequences are literal",
        "Authorization precedes object lookup",
    ):
        require(
            clause in file_path["schema"]["description"] and clause in directory["schema"]["description"],
            f"Shared file/directory protection lost: {clause}",
        )
    for operation_id in (
        "get_project_file_content",
        "list_project_files",
        "get_reference_solution_file_content",
        "list_reference_solution_files",
        "list_admin_case_files",
        "get_admin_case_file_content",
        "list_public_case_files",
        "get_public_case_file_content",
    ):
        path, op = by_id[operation_id]
        require(
            op["responses"]["422"] == {"$ref": "#/components/responses/ValidationError"}, f"Missing422: {operation_id}"
        )
        require("404" in op["responses"], f"Missing hidden/missing404: {operation_id}")
        effective = [deref(doc, p) for p in doc["paths"][path].get("parameters", []) + op.get("parameters", [])]
        path_params = [p for p in effective if p["name"] == "path" and p["in"] == "query"]
        listing = operation_id.startswith("list_")
        require(path_params == [directory if listing else file_path], f"Inconsistent path parameter: {operation_id}")
        policy = policies["directory_reads" if listing else "content_reads"]
        require(
            policy in op["description"] and policy in op["responses"]["200"]["description"],
            f"Read parameters/response/prose incoherent: {operation_id}",
        )
        if listing:
            require(op["x-ordering"] == "path-asc-utf8-bytewise", "Directory order must be deterministic path ASC")
            require("collections sort created_at" not in op["description"], "Directory must not inherit default order")
            require({"limit", "offset"} <= {p["name"] for p in effective}, "Directory pagination missing")
    source = by_id["get_assigned_source_review"][1]
    source_policy = policies["assigned_source_read"]
    require(
        source["responses"]["404"] == {"$ref": "#/components/responses/NotFound"}
        and source_policy in source["responses"]["200"]["description"]
        and source_policy in source["responses"]["409"]["description"]
        and source_policy in source["description"]
        and source_policy in schemas["AssignedReviewContent"]["description"]
        and source_policy == doc["x-operation-access"]["assigned-source-review"],
        "Source read must fail closed consistently across response/schema/access",
    )
    for code in ("404", "409"):
        result = deref(doc, source["responses"][code])
        require(
            set(result["content"]) == {"application/json"}
            and result["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Error"},
            f"Source fail-closed{code} must serialize Error only, never source content",
        )
    error = schemas["Error"]
    require(
        error["additionalProperties"] is False
        and set(error["properties"]) == {"code", "message", "request_id", "details"}
        and set(error["required"]) == {"code", "message", "request_id"}
        and all(error["properties"][key]["type"] == "string" for key in ("code", "message", "request_id"))
        and error["properties"]["details"] == {"type": "object", "properties": {}, "additionalProperties": False},
        "Safe Error envelope cannot carry source/revision objects or unbounded arbitrary details",
    )
    for clause in (
        "CURRENT confirmed expert grants in ALL case skills",
        "foreign, hidden or revoked access404",
        "completed409 ASSIGNMENT_NOT_ACTIVE",
        "expired/cancelled or elapsed lease409 ASSIGNMENT_EXPIRED",
        "assignment.revision_id == review.current_revision.id",
        "current quality job/assignment",
        "review.status=pending_quality_check",
        "consistent DB snapshot",
        "no source/revision content",
    ):
        require(clause in source_policy, f"Source fail-closed condition missing: {clause}")
    require(
        set(schemas["AssignedReviewContent"]["x-semantic-validation"])
        == {
            "live-current-quality-assignment-only",
            "revision == review.current_revision",
            "revision.id == assignment.revision_id",
            "submission.id == review.submission_id",
            "consistent-db-snapshot-no-other-revision-content",
        },
        "Nested current revision cannot differ from assignment-pinned content",
    )
    for operation_id in ("get_review_result", "get_review_revision"):
        require(
            "expired/completed" in by_id[operation_id][1]["description"],
            "Old claim must not grant alternate pending read",
        )
    history = policies["priority_history"]
    for clause in (
        "Only the owner",
        "reserved, settled, cancelled or refunded",
        "ALL",
        "Foreign/hidden singleton404",
        "No waiting-job",
    ):
        require(clause in history, f"Priority history scope incomplete: {clause}")
    for operation_id in ("get_priority_request", "list_my_priority_requests"):
        op = by_id[operation_id][1]
        require(
            history in op["description"]
            and policies["priority"] not in op["description"]
            and history == op["responses"]["200"]["description"],
            "Purchase predicates must not gate priority history",
        )
    require(history in schemas["PriorityRequest"]["description"], "Priority schema must preserve terminal history")
    read_rule = policies["readiness_read"]
    for clause in (
        "Applicant may self-read",
        "CURRENT CONFIRMED",
        "SAME competency",
        "grade=expert, grant=confirmed",
        "expert_confirmed=true",
        "No readiness assignment/assessor registry is required",
        "unrelated/supervised/revoked",
        "assessment.application_id equals the path application_id",
    ):
        require(clause in read_rule, f"Readiness access condition missing: {clause}")
    for operation_id in ("get_reviewer_application", "get_readiness_assessment", "list_my_reviewer_applications"):
        op = by_id[operation_id][1]
        require(
            read_rule in op["description"] and read_rule in op["responses"]["200"]["description"],
            f"Readiness GET authorization drift: {operation_id}",
        )
    require(
        policies["readiness_assess"] in by_id["create_readiness_assessment"][1]["description"]
        and policies["readiness_assess"] == schemas["ReadinessAssessmentRequest"]["description"]
        and "Applicant POST403 SELF_ASSESSMENT" in policies["readiness_assess"]
        and "No readiness assignment/assessor registry is required" in policies["readiness_assess"],
        "Readiness POST must explicitly authorize current independent same-skill experts",
    )
    for operation_id, keys, policy, schema in (
        ("create_priority_request", ["job_id"], "priority_deduplication", "PriorityRequest"),
        (
            "create_reviewer_application",
            ["applicant_id", "competency_id", "requested_grade"],
            "readiness_deduplication",
            "ReviewerApplication",
        ),
    ):
        op = by_id[operation_id][1]
        require(
            op["x-business-deduplication"]
            == {
                "unique_by": keys,
                "lifetime": "active-attempt-with-permanent-history",
                "new_or_expired_request_key": "equal-facts-existing-200-changed-facts-409",
                "duplicate_effects": False,
                "policy": policy,
            },
            f"Active business-key deduplication lost: {operation_id}",
        )
        rule = policies[policy]
        require(
            rule in op["description"]
            and rule == op["responses"]["200"]["description"]
            and rule in schemas[schema]["description"]
            and op["responses"]["200"]["headers"]["Location"] == op["responses"]["201"]["headers"]["Location"]
            and op["responses"]["200"]["content"] == op["responses"]["201"]["content"],
            f"Business200 response binding/prose lost: {operation_id}",
        )
    require(
        "no duplicate hold, debit, request" in policies["priority_deduplication"]
        and "Changed facts while reserved give409 PRIORITY_ALREADY_ACTIVE" in policies["priority_deduplication"]
        and "fresh request/reservation/source IDs" in policies["priority_deduplication"]
        and "does not replace that pending snapshot" in policies["readiness_deduplication"]
        and "Changed facts for an existing pending skill application409 APPLICATION_ALREADY_PENDING"
        in policies["readiness_deduplication"]
        and "fresh application ID/evidence snapshot201" in policies["readiness_deduplication"],
        "New/expired key must not duplicate active effects or erase terminal attempt history",
    )


def check_ledger_sequence_example(rows):
    """One owner's contract example, not a database order/authorization implementation."""
    sequences = [row["sequence"] for row in rows]
    require(all(type(sequence) is int and sequence > 0 for sequence in sequences), "Ledger sequence must be positive")
    require(len(set(sequences)) == len(sequences), "Ledger sequence must be unique per owner")
    require(sequences == sorted(sequences, reverse=True), "Ledger order must be sequence DESC")


def check_examples(doc):
    # OpenAPI's own schema dialect handles nullable and boolean exclusiveMinimum.
    from openapi_schema_validator import OAS30Validator

    count = 0

    def validate(schema, example):
        nonlocal count
        validator = OAS30Validator(expand_schema(doc, schema), format_checker=OAS30Validator.FORMAT_CHECKER)
        validator.validate(example)
        count += 1

    def schema_examples(schema):
        if not isinstance(schema, dict):
            return
        if "example" in schema:
            validate(schema, schema["example"])
        for field in ("properties",):
            for child in schema.get(field, {}).values():
                schema_examples(child)
        if isinstance(schema.get("items"), dict):
            schema_examples(schema["items"])
        for key in ("allOf", "oneOf", "anyOf"):
            for child in schema.get(key, []):
                schema_examples(child)

    for schema in doc["components"]["schemas"].values():
        schema_examples(schema)
    for path, node in walk(doc):
        if "schema" not in node or not isinstance(node["schema"], dict):
            continue
        schema_examples(node["schema"])
        if "example" in node:
            validate(node["schema"], node["example"])
        for raw in node.get("examples", {}).values():
            example = deref(doc, raw)
            require("externalValue" not in example, f"Cannot validate external example: {path}")
            validate(node["schema"], example["value"])
    require(count > 0, "No examples validated")
    return count


def validate_case_skills(skills):
    """Contract semantic oracle only, NOT backend implementation or live proof."""
    ids = [skill["competency_id"] for skill in skills]
    require(bool(ids) and len(ids) == len(set(ids)), "DUPLICATE_COMPETENCY")
    for skill in skills:
        require(Decimal(0) <= Decimal(str(skill["required_value"])) <= Decimal(100), "required_value bounds")
        require(Decimal(0) < Decimal(str(skill["weight"])) <= Decimal("0.25"), "weight bounds")


def contract_eligible(skills, author_grades, reviewer):
    """Illustrative qualification oracle; actual DB/auth/concurrency tested later."""
    grades = ["performer", "review_minus", "review_plus", "expert"]
    validate_case_skills(skills)
    for skill in skills:
        key = skill["competency_id"]
        actor = reviewer.get(key)
        required = Decimal(str(skill["required_value"]))
        case_floor = 3 if required >= 75 else 2 if required >= 50 else 1
        floor = max(case_floor, min(3, grades.index(author_grades.get(key, "performer")) + 1))
        if not actor or actor["grant"] not in {"confirmed", "supervised"}:
            return False
        if Decimal(str(actor["value"])) < required or grades.index(actor["grade"]) < floor:
            return False
        if (floor == 3 or actor["grade"] == "expert") and not (
            actor["grade"] == "expert" and actor["expert_confirmed"] and actor["grant"] == "confirmed"
        ):
            return False
    return True


def contract_path(raw, *, directory=False, traversed_types=()):
    """Example oracle for one transport decode; no filesystem/Git access or runtime proof."""
    if raw is None:
        require(directory, "INVALID_PATH")
        raw = ""
    try:
        path = unquote(raw, encoding="utf-8", errors="strict")
        size = len(path.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("INVALID_PATH") from exc
    require(size <= 1024, "INVALID_PATH")
    require(not {"symlink", "submodule"} & set(traversed_types), "INVALID_PATH")
    if directory and not path:
        return path
    require(bool(path) and "\\" not in path and "\0" not in path, "INVALID_PATH")
    require(all(part.lower() not in {"", ".", "..", ".git"} for part in path.split("/")), "INVALID_PATH")
    if traversed_types:
        require(traversed_types[-1] == ("dir" if directory else "file"), "INVALID_PATH")
        require(all(kind == "dir" for kind in traversed_types[:-1]), "INVALID_PATH")
    return path


def contract_source_read(facts):
    """Fail-closed source-read example oracle, not application authorization."""
    if not facts["owned"] or not facts["current_expert"]:
        return 404, "NOT_FOUND"
    if facts["kind"] == "recheck":
        return 501, "FEATURE_DEFERRED"
    if facts["kind"] != "quality_check":
        return 409, "ASSIGNMENT_KIND_MISMATCH"
    if facts["status"] in {"expired", "cancelled"} or not facts["lease_live"]:
        return 409, "ASSIGNMENT_EXPIRED"
    if facts["status"] != "assigned":
        return 409, "ASSIGNMENT_NOT_ACTIVE"
    if (
        not facts["current_assignment"]
        or facts["pinned_revision_id"] != facts["current_revision_id"]
        or facts["review_status"] != "pending_quality_check"
    ):
        return 409, "STALE_REVISION"
    return 200, None


def contract_source_payload(facts, payload):
    """Even a successful authorization cannot serialize an independently fetched latest revision."""
    require(contract_source_read(facts) == (200, None), "Source read denied")
    require(
        payload["revision"] == payload["review"]["current_revision"]
        and payload["revision"]["id"] == facts["pinned_revision_id"]
        and payload["submission"]["id"] == payload["review"]["submission_id"],
        "Mixed source revision/submission content",
    )


def contract_active_attempt(resource, state, equal_facts, eligible_new):
    """New/expired HTTP keys share the same business oracle; no DB/ledger implementation."""
    active = "reserved" if resource == "priority" else "pending"
    if state == active:
        return 200 if equal_facts else 409
    if resource == "priority" and state == "settled":
        return 409
    return 201 if eligible_new else 409


def contract_readiness_access(*, applicant, competency_id, skills, method, assessment_matches=True):
    """Explicit owner/current same-skill access examples; no implicit assessor assignment."""
    if not assessment_matches:
        return 404
    if applicant:
        return 200 if method == "get" else 403
    skill = skills.get(competency_id, {})
    qualified = (
        skill.get("grade") == "expert"
        and skill.get("grant") == "confirmed"
        and skill.get("expert_confirmed") is True
        and skill.get("current") is True
    )
    return (200 if method == "get" else 201) if qualified else 404


def check_behavior_examples(doc):
    examples = doc["x-contract-scenarios"]
    require(examples["scope"] == "contract-examples-only-not-runtime-proof", "Scenarios are not implementation proof")
    for example in examples["source_review"]:
        result = contract_source_read(example["facts"])
        require(result == (example["status"], example["code"]), f"Invalid source example: {example['name']}")
        visible = [example["facts"]["pinned_revision_id"]] if result[0] == 200 else []
        require(example["visible_revision_ids"] == visible, "Denied source read leaks revision content")
    for example in examples["active_attempts"]:
        status = contract_active_attempt(
            example["resource"],
            example["state"],
            example["equal_facts"],
            example["eligible_new"],
        )
        require(status == example["status"], f"Invalid active-attempt example: {example['name']}")
        require(
            example["same_identity"] is (status == 200)
            and example["new_effects"] is (status == 201)
            and example["replace_evidence"] is False,
            "Existing active attempts cannot duplicate effects/replace evidence",
        )
    return len(examples["source_review"]) + len(examples["active_attempts"])


def check(root=ROOT):
    from openapi_spec_validator import validate

    original_path = root / "prod-guild-openapi-v0.7-rest.json"
    require(hashlib.sha256(original_path.read_bytes()).hexdigest() == ORIGINAL_HASH, "Supplied v0.7 changed")
    doc = json.loads((root / "prodguild-openapi-v0.8-rest.json").read_text())
    original = json.loads(original_path.read_text())
    summary = check_structure(doc, original)  # Forbid remote refs BEFORE using third-party validators.
    validate(doc)
    summary["validated_examples"] = check_examples(doc)
    summary["validated_behavior_examples"] = check_behavior_examples(doc)
    summary["state"] = "candidate-not-frozen"
    summary["review_response_capture"] = "blocked-pending-coordinated-design; binding-held"
    summary["wallet_sequence_mapper"] = "proposal-pending-t11-coordinator-signoff"
    summary["live_acl_tests"] = (
        "not executed by this validator or the independent reviewer; sole-lead-owner run prodguild-t01-lab-bf59da5559 records66/66 existing named corrected local observations; historical evidence disputed, independent review corrections-only, all component/full gates held"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
