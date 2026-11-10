"""Candidate-contract regressions; these tests are NOT live Gitea ACL tests."""

import copy
import json
import unittest
from decimal import Decimal

from build_contract import build
from check_contract import (
    ROOT,
    check,
    check_behavior_examples,
    check_examples,
    check_ledger_sequence_example,
    check_structure,
    contract_active_attempt,
    contract_eligible,
    contract_path,
    contract_readiness_access,
    contract_source_payload,
    contract_source_read,
    expand_schema,
    operations,
    validate_case_skills,
)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads((ROOT / "prodguild-openapi-v0.8-rest.json").read_text())
        cls.original = json.loads((ROOT / "prod-guild-openapi-v0.7-rest.json").read_text())

    def test_complete_openapi_and_examples(self):
        result = check()
        self.assertEqual(result["original_operations"], 60)
        self.assertEqual(result["original_access_rules_checked"], 60)
        self.assertGreater(result["validated_examples"], 10)

    def test_generated_artifact_is_reproducible(self):
        self.assertEqual(self.doc, build())
        self.assertEqual(
            (ROOT / "prodguild-openapi-v0.8-rest.json").read_text(),
            json.dumps(build(), indent=2, ensure_ascii=False) + "\n",
        )

    def reject(self, mutate):
        candidate = copy.deepcopy(self.doc)
        mutate(candidate)
        with self.assertRaises((ValueError, KeyError)):
            check_structure(candidate, self.original)

    def test_missing_original_operation_rejected(self):
        self.reject(lambda d: d["paths"].pop("/api/v1/me/review-mode"))

    def test_all_original_visibility_rules_survive_blanket_description_changes(self):
        for path, method in operations(self.original):
            with self.subTest(path=path, method=method):
                self.reject(lambda d: d["paths"][path][method].pop("x-access-rule"))
                self.reject(
                    lambda d: d["paths"][path][method].update(description="Authenticated resource-scoped access.")
                )

    def test_reference_reads_require_case_owner_not_any_organizer(self):
        base = "/api/v1/admin/cases/{case_id}/reference-solution"
        for suffix in ("", "/files", "/files/content"):
            path = base + suffix
            with self.subTest(path=path):
                self.assertEqual(self.doc["paths"][path]["get"]["x-access-rule"], "case-owner-organizer-only")
                self.reject(lambda d: d["paths"][path]["get"].update({"x-access-rule": "authenticated-policy"}))
        self.reject(
            lambda d: d["x-operation-access"].update(
                {"case-owner-organizer-only": "Any organizer may read references."}
            )
        )

    def test_assignment_workspace_draft_and_source_reads_are_assignment_scoped(self):
        base = "/api/v1/review-assignments/{assignment_id}"
        for suffix, scope in (
            ("", "assigned-reviewer-only"),
            ("/draft", "assigned-reviewer-only"),
            ("/workspace", "assigned-review-workspace"),
            ("/source-review", "assigned-source-review"),
        ):
            path = base + suffix
            with self.subTest(path=path):
                self.assertEqual(self.doc["paths"][path]["get"]["x-access-rule"], scope)
                self.reject(lambda d: d["paths"][path]["get"].update({"x-access-rule": "pinned-submission-read"}))

    def test_catalogue_and_material_reads_require_published_cases(self):
        for suffix in ("", "/{case_id}", "/{case_id}/files", "/{case_id}/files/content"):
            path = "/api/v1/cases" + suffix
            with self.subTest(path=path):
                self.assertEqual(self.doc["paths"][path]["get"]["x-access-rule"], "published-case-only")
                self.reject(lambda d: d["paths"][path]["get"].update({"x-access-rule": "authenticated-policy"}))
        self.reject(
            lambda d: d["x-operation-access"].update(
                {"published-case-only": "All authenticated users may read any case."}
            )
        )

    def test_project_working_tree_and_final_progression_remain_private(self):
        for path, scope in (
            ("/api/v1/projects/{project_id}", "project-owner-only"),
            ("/api/v1/projects/{project_id}/files/content", "project-owner-only"),
            ("/api/v1/submissions/{submission_id}/progression", "published-progression-read"),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.doc["paths"][path]["get"]["x-access-rule"], scope)
                self.reject(lambda d: d["paths"][path]["get"].update({"x-access-rule": "authenticated-policy"}))

    def test_project_and_submission_business_keys_outlive_request_keys(self):
        for path, keys in (
            ("/api/v1/me/projects", ["participant_id", "case_id"]),
            ("/api/v1/projects/{project_id}/submissions", ["project_id"]),
        ):
            self.assertEqual(self.doc["paths"][path]["post"]["x-business-deduplication"]["unique_by"], keys)
            for field, value in (
                ("unique_by", ["idempotency_key"]),
                ("lifetime", "24-hours"),
                ("new_or_expired_request_key", "create-another-201"),
                ("duplicate_git_resources", True),
            ):
                with self.subTest(path=path, field=field):
                    self.reject(lambda d: d["paths"][path]["post"]["x-business-deduplication"].update({field: value}))

    def test_unchanged_existing200_is_not_only_cached_request_replay(self):
        for path in ("/api/v1/me/projects", "/api/v1/projects/{project_id}/submissions"):
            with self.subTest(path=path):
                self.reject(
                    lambda d: d["paths"][path]["post"]["responses"]["200"].update(
                        description="Same request-key replay only."
                    )
                )
                self.reject(lambda d: d["paths"][path]["post"]["responses"]["200"].pop("headers"))
        rule = self.doc["x-business-decisions"]["submission_uniqueness"]
        for clause in (
            "One Submission per project_id",
            "new or expired request key",
            "unchanged repeat",
            "PROJECT_SUBMITTED",
        ):
            self.assertIn(clause, rule)

    def test_accidental_freeze_rejected(self):
        self.reject(lambda d: d["x-contract-status"].update(state="frozen"))

    def test_unaccepted_full_isolation_proof_rejected(self):
        self.reject(lambda d: d["x-contract-status"].update(isolation_proven=True))

    def test_local_evidence_does_not_release_contract_or_production(self):
        status = self.doc["x-contract-status"]
        self.assertEqual(status["local_evidence"]["state"], "owner-run-reviewed-corrections-local-bounded-observations")
        self.assertTrue(status["local_evidence"]["local_read_write_matrix_complete"])
        self.assertEqual(status["independent_review"], "pending")
        self.assertEqual(status["coordinator_acceptance"], "pending")
        self.assertFalse(status["downstream_released"])
        self.assertTrue(all(value is False for value in status["production_gates"].values()))

    def test_untested_release_or_environment_rejected(self):
        for field, value in (("release", "unverified"), ("image", "gitea:latest"), ("environment", "production")):
            with self.subTest(field=field):
                self.reject(lambda d: d["x-contract-status"]["local_evidence"].update({field: value}))

    def test_historical_matrix_remains_scoped_disputed_and_pending_rerun(self):
        self.reject(
            lambda d: d["x-contract-status"]["local_evidence"].update(
                evidence_scope="all-gitea-and-production-behavior"
            )
        )
        self.reject(lambda d: d["x-contract-status"]["local_evidence"].pop("historical_evidence_path"))
        for check_name in (
            "authenticated-browser-login-and-account-authorized-links",
            "file-blob-concurrency-lock-ref-403-and-stale-sha-422",
        ):
            with self.subTest(check_name=check_name):
                self.reject(
                    lambda d: d["x-contract-status"]["local_evidence"]["historical_observations"].remove(check_name)
                )
        for key, value in (
            ("local_read_write_matrix_complete", False),
            ("new_live_proof", False),
            ("latest_corrected_evidence", "passed"),
            ("historical_reported_rows", 67),
            ("dispute", "resolved"),
        ):
            with self.subTest(key=key):
                self.reject(lambda d: d["x-contract-status"]["local_evidence"].update({key: value}))
        for gate in ("component_gate", "full_gate"):
            self.reject(lambda d: d["x-contract-status"].update({gate: "released"}))

    def test_local_evidence_cannot_imply_coordinator_or_production_approval(self):
        self.reject(lambda d: d["x-contract-status"].update(coordinator_acceptance="accepted"))
        self.reject(lambda d: d["x-contract-status"].update(independent_review="accepted"))
        self.reject(lambda d: d["x-contract-status"].update(downstream_released=True))
        for gate in ("mirror_approved", "postgresql_validated", "vm_deployment_approved"):
            with self.subTest(gate=gate):
                self.reject(lambda d: d["x-contract-status"]["production_gates"].update({gate: True}))

    def test_service_only_or_shared_upstream_topology_rejected(self):
        for topology in ("service-only-assessment-repo", "shared-upstream-private-fork-pr"):
            with self.subTest(topology=topology):
                self.reject(lambda d: d["x-git-boundaries"].update(topology=topology))
        self.reject(lambda d: d["x-git-boundaries"].update(shared_upstream_private_fork_prs="allowed"))
        self.reject(lambda d: d["x-git-boundaries"].update(reference_and_draft_repositories="hidden-project-branches"))

    def test_pr_links_allow_authorized_urls_and_unavailable_null(self):
        from openapi_schema_validator import OAS30Validator

        schema = self.doc["components"]["schemas"]["Submission"]["properties"]["pull_request_url"]
        validator = OAS30Validator(schema, format_checker=OAS30Validator.FORMAT_CHECKER)
        validator.validate("https://git.example.invalid/projects/learner-a/pulls/1")
        validator.validate(None)
        # URL shape is not ACL proof: the contract separately requires viewer-account authorization.
        self.reject(lambda d: d["x-git-boundaries"]["links"].update(viewer_account_acl_required=False))
        self.reject(lambda d: d["x-git-boundaries"]["links"].update(credential_free=False))

    def test_incidental_annotations_cannot_withhold_authorized_pr_links(self):
        self.reject(lambda d: d["x-git-boundaries"]["links"].update(incidental_annotations_block_pr_links=True))
        self.reject(lambda d: d["x-git-boundaries"]["links"].update(authorized_read_collaborator_pr_links=False))
        self.reject(lambda d: d["x-git-boundaries"]["links"]["nullable_pr_reasons"].append("incidental-comments"))

    def test_incidental_annotations_cannot_become_business_authority(self):
        self.reject(lambda d: d["x-git-boundaries"].update(authoritative_store="Gitea"))
        self.reject(lambda d: d["x-git-boundaries"]["incidental_annotations"].update(trust="trusted"))
        self.reject(lambda d: d["x-git-boundaries"]["incidental_annotations"].update(business_effects=True))
        self.reject(lambda d: d["x-git-boundaries"]["projection"].update(comment_metadata_authority="Gitea"))

    def test_pending_and_restricted_content_cannot_enter_learner_git(self):
        for field in ("pending_drafts", "pending_quality_feedback", "restricted_quality_results"):
            with self.subTest(field=field):
                self.reject(lambda d: d["x-git-boundaries"]["projection"].update({field: "learner-readable-git"}))
        self.reject(lambda d: d["x-git-boundaries"]["projection"].update(source="pending-review-revision"))

    def test_learner_cannot_author_or_mutate_canonical_projection(self):
        self.reject(lambda d: d["x-git-boundaries"]["projection"].update(writer="learner-account"))
        self.reject(
            lambda d: d["x-git-boundaries"]["projection"].update(learner_can_edit_or_delete_canonical_projection=True)
        )

    def test_gitea_write_conflicts_are_not_classified_by_status_alone(self):
        self.reject(lambda d: d["x-gitea-compatibility"].update(write_error_classification="all-403-are-acl-denials"))
        for index, expected in enumerate(((403, 409, "CONFLICT"), (422, 409, "STALE_BLOB"))):
            row = self.doc["x-gitea-compatibility"]["recognized_write_conflicts"][index]
            self.assertEqual((row["gitea_status"], row["api_status"], row["api_code"]), expected)
            with self.subTest(index=index):
                self.reject(
                    lambda d: d["x-gitea-compatibility"]["recognized_write_conflicts"][index].update(api_status=403)
                )

    def test_native_position_acceptance_never_replaces_app_diff_validation(self):
        compatibility = self.doc["x-gitea-compatibility"]
        self.assertEqual(
            set(compatibility["observed_unvalidated_positions"]),
            {
                "new_position=999-empty-hunk",
                "unchanged-README-line1",
                "both-positions-zero-empty-hunk",
            },
        )
        self.reject(lambda d: d["x-gitea-compatibility"].update(application_pinned_diff_validation_required=False))
        self.reject(lambda d: d["x-gitea-compatibility"].update(upstream_comment_acceptance_proves_valid_position=True))
        self.reject(lambda d: d["x-gitea-compatibility"].update(file_comment_projection="native-zero-position"))
        self.reject(
            lambda d: d["components"]["schemas"]["ReviewComment"].update(
                {"x-semantic-validation": ["line=null iff side=null"]}
            )
        )

    def test_duplicate_operation_id_rejected(self):
        self.reject(lambda d: d["paths"]["/ready"]["get"].update(operationId="get_healthcheck"))

    def test_external_ref_rejected_before_network(self):
        self.reject(lambda d: d["components"]["schemas"]["Health"].update({"$ref": "https://invalid.example/schema"}))

    def test_unresolved_ref_rejected(self):
        self.reject(lambda d: d["components"]["schemas"].pop("Health"))

    def test_external_unused_component_example_rejected(self):
        self.reject(
            lambda d: d["components"].update(examples={"Unused": {"externalValue": "https://invalid.example/example"}})
        )

    def test_mandatory_snapshot_removal_rejected(self):
        for name, field in [
            ("Submission", "author_skills"),
            ("ReviewRevision", "reviewer_skills"),
            ("ReviewTask", "reviewer_skills"),
            ("ReviewQualityAssessment", "assessor_skills"),
        ]:

            def remove(d):
                d["components"]["schemas"][name]["properties"].pop(field)
                d["components"]["schemas"][name]["required"].remove(field)

            with self.subTest(name=name):
                self.reject(remove)

    def test_snapshot_relationship_mutation_rejected(self):
        self.reject(
            lambda d: d["components"]["schemas"]["Submission"]["properties"]["author_skills"].update(
                items={"type": "object"}
            )
        )

    def test_large_workspace_and_pinned_diff_are_not_native_pr_dependent(self):
        s = self.doc["components"]["schemas"]
        self.assertTrue(s["ReviewWorkspace"]["properties"]["snapshot"]["nullable"])
        self.assertIn("paginated", s["ReviewWorkspace"]["properties"]["snapshot_state"]["enum"])
        for suffix in ("files", "files/content", "diff", "diff/lines"):
            self.assertIn("get", self.doc["paths"]["/api/v1/submissions/{submission_id}/" + suffix])
        self.assertIn("base_retained_ref", s["Submission"]["required"])

    def test_expert_source_review_large_snapshot_fallback(self):
        from openapi_schema_validator import OAS30Validator

        source = self.doc["components"]["schemas"]["AssignedReviewContent"]
        # >200 files /2MiB uses pagination, not a failed source review or HEAD.
        fields = ("snapshot", "snapshot_state", "files_url", "diff_url")
        schema = {
            "type": "object",
            "properties": {k: source["properties"][k] for k in fields},
            "required": list(fields),
        }
        OAS30Validator(expand_schema(self.doc, schema)).validate(
            {
                "snapshot": None,
                "snapshot_state": "paginated",
                "files_url": "/api/v1/submissions/00000000-0000-4000-8000-000000000001/files",
                "diff_url": "/api/v1/submissions/00000000-0000-4000-8000-000000000001/diff",
            }
        )
        self.assertTrue(set(fields) <= set(source["required"]))

    def test_quality_location_collection_regression_rejected(self):
        path = "/api/v1/reviews/{review_id}/quality-assessments"
        self.reject(lambda d: d["paths"][path]["post"]["responses"]["201"]["headers"]["Location"].update(example=path))

    def test_readiness_feedback_is_persistently_readable(self):
        self.assertIn("latest_assessment", self.doc["components"]["schemas"]["ReviewerApplication"]["required"])
        self.assertIn(
            "get", self.doc["paths"]["/api/v1/reviewer-applications/{application_id}/assessments/{assessment_id}"]
        )

    def test_all_eight_validated_file_reads_document_422(self):
        ids = {
            "get_project_file_content",
            "list_project_files",
            "get_reference_solution_file_content",
            "list_reference_solution_files",
            "list_admin_case_files",
            "get_admin_case_file_content",
            "list_public_case_files",
            "get_public_case_file_content",
        }
        for (path, method), op in operations(self.doc).items():
            if op["operationId"] in ids:
                with self.subTest(operation_id=op["operationId"]):
                    self.reject(lambda d: d["paths"][path][method]["responses"].pop("422"))
                    self.reject(lambda d: d["paths"][path][method]["responses"]["200"].update(description="Any path"))

    def test_directory_parameter_and_order_cannot_inherit_unbounded_path_or_default_sort(self):
        self.reject(lambda d: d["components"]["parameters"]["DirectoryPath"]["schema"].pop("maxLength"))
        self.reject(lambda d: d["components"]["parameters"]["DirectoryPath"].update(required=True))
        self.reject(lambda d: d["components"]["parameters"]["DirectoryPath"]["schema"].update(minLength=1))
        for path in (
            "/api/v1/projects/{project_id}/files",
            "/api/v1/admin/cases/{case_id}/reference-solution/files",
            "/api/v1/admin/cases/{case_id}/files",
            "/api/v1/cases/{case_id}/files",
        ):
            with self.subTest(path=path):
                self.reject(lambda d: d["paths"][path]["get"].update({"x-ordering": "created_at-desc-id-asc"}))
                self.reject(
                    lambda d: d["paths"][path]["get"]["parameters"].__setitem__(
                        0,
                        {
                            "name": "path",
                            "in": "query",
                            "schema": {"type": "string", "default": ""},
                        },
                    )
                )

    def test_source_read_errors_and_nested_revision_binding_are_guarded(self):
        path = "/api/v1/review-assignments/{assignment_id}/source-review"
        for status in ("404", "409"):
            self.reject(lambda d: d["paths"][path]["get"]["responses"].pop(status))
        self.reject(lambda d: d["components"]["schemas"]["AssignedReviewContent"].update({"x-semantic-validation": []}))
        self.reject(lambda d: d["paths"][path]["get"]["responses"]["200"].update(description="Any owned assignment"))

    def test_source_fail_closed_responses_cannot_substitute_source_content_for_error(self):
        path = "/api/v1/review-assignments/{assignment_id}/source-review"
        for code in ("404", "409"):
            for target in ("AssignedReviewContent", "ReviewResult", "ReviewRevision"):

                def substitute(d):
                    raw = d["paths"][path]["get"]["responses"][code]
                    response = d["components"]["responses"][raw["$ref"].rsplit("/", 1)[-1]] if "$ref" in raw else raw
                    response["content"]["application/json"]["schema"] = {"$ref": f"#/components/schemas/{target}"}

                with self.subTest(code=code, target=target):
                    self.reject(substitute)
        self.reject(
            lambda d: d["paths"][path]["get"]["responses"]["409"]["content"].update(
                {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                }
            )
        )
        self.reject(
            lambda d: d["components"]["schemas"]["Error"]["properties"].update(
                source={"$ref": "#/components/schemas/AssignedReviewContent"}
            )
        )
        self.reject(
            lambda d: d["components"]["schemas"]["Error"]["properties"]["details"].update(additionalProperties=True)
        )

    def test_oversized_valid_review_payload_exposes_real_capture_blocker(self):
        from openapi_schema_validator import OAS30Validator

        schemas = self.doc["components"]["schemas"]
        draft = copy.deepcopy(schemas["ReviewDraft"]["example"])
        draft["comments"] = [{"path": "a", "line": None, "side": None, "body": "x" * 10000} for _ in range(200)]
        request = {
            "expected_version": draft["version"],
            **{key: draft[key] for key in ("score", "verdict", "feedback", "comments")},
        }
        for name, payload in (("SaveReviewDraftRequest", request), ("ReviewDraft", draft)):
            OAS30Validator(expand_schema(self.doc, schemas[name])).validate(payload)
        serialized = json.dumps(draft, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ceiling = self.doc["x-prodguild-integration-boundary"]["review_response_capture"][
            "t03_serialized_response_max_bytes"
        ]
        self.assertGreater(len(serialized), ceiling)
        self.assertGreater(len(serialized), 2000000)
        self.assertFalse(self.doc["x-prodguild-integration-boundary"]["review_response_capture"]["compatible_with_t03"])

    def test_review_response_binding_cannot_silently_become_compatible(self):
        for key, value in (
            ("binding_gate", "released"),
            ("compatible_with_t03", True),
            ("mapper_implemented", True),
            ("proposal_accepted", True),
            ("t03_serialized_response_max_bytes", 4194304),
        ):
            with self.subTest(key=key):
                self.reject(
                    lambda d: d["x-prodguild-integration-boundary"]["review_response_capture"].update({key: value})
                )
        for path, method in (
            ("/api/v1/review-assignments/{assignment_id}/draft", "put"),
            ("/api/v1/reviews/{review_id}/revisions", "post"),
            ("/api/v1/reviews/{review_id}", "get"),
        ):
            self.reject(lambda d: d["paths"][path][method].pop("x-prodguild-integration-gate"))
        self.reject(lambda d: d["components"]["schemas"]["ReviewComment"]["properties"]["body"].update(maxLength=1000))

    def test_wallet_history_requires_positive_immutable_owner_sequence(self):
        from jsonschema.exceptions import ValidationError
        from openapi_schema_validator import OAS30Validator

        schema = self.doc["components"]["schemas"]["WalletTransaction"]
        validator = OAS30Validator(expand_schema(self.doc, schema))
        for sequence in (0, -1, 1.5):
            with self.subTest(sequence=sequence), self.assertRaises(ValidationError):
                validator.validate({**schema["example"], "sequence": sequence})
        self.reject(lambda d: d["components"]["schemas"]["WalletTransaction"]["required"].remove("sequence"))
        self.reject(lambda d: d["components"]["schemas"]["WalletTransaction"].update({"x-sequence-scope": "global"}))
        self.reject(lambda d: d["x-prodguild-integration-boundary"]["wallet_history"].update(mapper_implemented=True))

    def test_wallet_history_orders_by_sequence_not_timestamp_or_id_and_includes_all_kinds(self):
        path = "/api/v1/me/wallet/transactions"
        rows = self.doc["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["example"]
        self.assertEqual(len({row["created_at"] for row in rows}), 1)
        self.assertEqual(len({row["kind"] for row in rows}), 7)
        check_ledger_sequence_example(rows)
        with self.assertRaisesRegex(ValueError, "sequence DESC"):
            check_ledger_sequence_example(list(reversed(rows)))
        with self.assertRaisesRegex(ValueError, "unique per owner"):
            check_ledger_sequence_example([rows[0], rows[0]])
        self.reject(lambda d: d["paths"][path]["get"].update({"x-ordering": "created_at-desc-id-asc"}))
        self.reject(lambda d: d["paths"][path]["get"].update(description=d["x-business-decisions"]["pagination"]))
        self.reject(
            lambda d: d["paths"][path]["get"]["responses"]["200"].update(description="All owners, timestamp order")
        )

    def test_priority_reads_keep_terminal_history_without_purchase_predicates(self):
        for path in ("/api/v1/priority-requests/{priority_request_id}", "/api/v1/me/priority-requests"):
            with self.subTest(path=path):
                self.reject(lambda d: d["paths"][path]["get"].update(description=d["x-business-decisions"]["priority"]))
        self.reject(lambda d: d["x-business-decisions"].update(priority_history="Owner may read waiting jobs only"))

    def test_readiness_reads_and_assessment_share_explicit_authorization(self):
        for path, method in (
            ("/api/v1/reviewer-applications/{application_id}", "get"),
            ("/api/v1/reviewer-applications/{application_id}/assessments/{assessment_id}", "get"),
            ("/api/v1/reviewer-applications/{application_id}/assessments", "post"),
        ):
            with self.subTest(path=path):
                self.reject(lambda d: d["paths"][path][method].update(description="Any assigned expert"))

    def test_active_attempt_dedup_and_location_do_not_expire_with_request_keys(self):
        for path in ("/api/v1/submissions/{submission_id}/priority-requests", "/api/v1/reviewer-applications"):
            for field, value in (
                ("lifetime", "24h"),
                ("new_or_expired_request_key", "always-create-201"),
                ("duplicate_effects", True),
            ):
                with self.subTest(path=path, field=field):
                    self.reject(lambda d: d["paths"][path]["post"]["x-business-deduplication"].update({field: value}))
            self.reject(
                lambda d: d["paths"][path]["post"]["responses"]["200"].update(description="Retained-key replay only")
            )
            self.reject(lambda d: d["paths"][path]["post"]["responses"]["200"].pop("headers"))

    def test_key_pattern_stays_length_only_until_coordinator_confirmation(self):
        self.reject(
            lambda d: d["components"]["parameters"]["IdempotencyKey"]["schema"].update(
                pattern="^[A-Za-z0-9._~-]{1,128}$"
            )
        )
        self.reject(lambda d: d["x-prodguild-integration-boundary"].update(implemented_adapter=True))
        self.reject(
            lambda d: d["x-prodguild-integration-boundary"]["idempotency_key"].update(
                t03_accepted_proposal="^[A-Za-z0-9._~-]{1,128}$"
            )
        )

    def test_behavior_examples_validate_and_reject_pending_revision_leak(self):
        self.assertEqual(check_behavior_examples(self.doc), 24)
        candidate = copy.deepcopy(self.doc)
        candidate["x-contract-scenarios"]["source_review"][1]["visible_revision_ids"] = ["revision-2"]
        with self.assertRaisesRegex(ValueError, "leaks revision"):
            check_behavior_examples(candidate)

    def test_active_attempt_examples_reject_duplicate_effect_and_evidence_replacement(self):
        for field in ("new_effects", "replace_evidence"):
            candidate = copy.deepcopy(self.doc)
            candidate["x-contract-scenarios"]["active_attempts"][0][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "duplicate effects/replace evidence"):
                check_behavior_examples(candidate)

    def test_file_mutation_response_caps_are_serialized_metadata_not_source_bytes(self):
        from jsonschema.exceptions import ValidationError
        from openapi_schema_validator import OAS30Validator

        payload = {
            "path": "main.py",
            "type": "file",
            "content": None,
            "sha": "a" * 40,
            "commit_sha": "b" * 40,
            "size": 1048576,
            "binary": False,
            "download_url": None,
        }
        for path in ("/api/v1/projects/{project_id}/files", "/api/v1/admin/cases/{case_id}/reference-solution/files"):
            op = self.doc["paths"][path]["post"]
            for code in ("200", "201"):
                shape = op["responses"][code]["content"]["application/json"]["schema"]
                validator = OAS30Validator(expand_schema(self.doc, shape))
                validator.validate(payload)
                with self.assertRaises(ValidationError):
                    validator.validate({**payload, "content": "even small content cannot be echoed"})
            self.reject(
                lambda d: d["paths"][path]["post"]["x-prodguild-response-boundary"].update(
                    serialized_success_json_max_bytes=1048576
                )
            )
            self.reject(
                lambda d: d["paths"][path]["post"]["responses"]["201"]["content"]["application/json"].update(
                    schema={"$ref": "#/components/schemas/ProjectFile"}
                )
            )
        raw_text = "\n" * 1048576
        self.assertEqual(len(raw_text.encode()), 1048576)
        self.assertGreater(len(json.dumps({**payload, "content": raw_text}).encode()), 1048576)
        self.assertLess(len(json.dumps(payload).encode()), 65536)
        get_shape = self.doc["paths"]["/api/v1/projects/{project_id}/files/content"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        OAS30Validator(expand_schema(self.doc, get_shape)).validate({**payload, "content": "authorized GET contents"})

    def test_new_correction_evidence_is_pending_and_never_revalidated_by_old_labels(self):
        self.reject(lambda d: d["x-prodguild-integration-boundary"]["live_evidence"].update(fresh_run="passed"))
        self.reject(
            lambda d: d["x-prodguild-integration-boundary"]["live_evidence"].update(
                historical_rows_revalidated_by_old_labels=True
            )
        )
        self.reject(
            lambda d: d["x-prodguild-integration-boundary"]["file_mutation_response"].update(mapper_implemented=True)
        )
        for parent in ("local_evidence", "live_evidence"):

            def point_to_old(d):
                container = (
                    d["x-contract-status"] if parent == "local_evidence" else d["x-prodguild-integration-boundary"]
                )
                container[parent]["latest_corrected_evidence_path"] = (
                    "tests/integration/gitea-spike/evidence/local-matrix.json"
                )

            with self.subTest(parent=parent):
                self.reject(point_to_old)

    def test_side_effectful_get_rejected(self):
        self.reject(lambda d: d["paths"]["/api/v1/me/projects"]["get"].update({"x-side-effects": True}))

    def test_missing_assignment_request_key_rejected(self):
        self.reject(lambda d: d["paths"]["/api/v1/me/review-assignments"]["post"].update(parameters=[]))

    def test_global_grade_authority_rejected(self):
        self.reject(
            lambda d: d["components"]["schemas"]["Submission"]["properties"].update(author_grade={"type": "string"})
        )

    def test_legacy_health_shape_rejected(self):
        self.reject(lambda d: d["components"]["schemas"]["Health"].update(example={"status": "ok"}))

    def test_invalid_example_rejected(self):
        from jsonschema.exceptions import ValidationError

        candidate = copy.deepcopy(self.doc)
        candidate["components"]["schemas"]["Wallet"]["example"]["available"] = -1
        with self.assertRaises(ValidationError) as caught:
            check_examples(candidate)
        self.assertIn("minimum", str(caught.exception))

    def test_request_key_not_business_deduplication(self):
        decisions = self.doc["x-business-decisions"]
        self.assertIn("business deduplication permanent", decisions["idempotency"])
        self.assertIn("NEW key", decisions["idempotency"])

    def test_comment_line_side_pairs(self):
        from jsonschema.exceptions import ValidationError
        from openapi_schema_validator import OAS30Validator

        validator = OAS30Validator(expand_schema(self.doc, self.doc["components"]["schemas"]["ReviewComment"]))
        base = {"path": "main.py", "body": "Explain error handling", "line": None, "side": None}
        validator.validate(base)
        validator.validate({**base, "line": 1, "side": "new"})
        for change in ({"line": 1}, {"side": "old"}, {"line": 0, "side": "new"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validator.validate({**base, **change})

    def test_quality_confirmation_threshold(self):
        from jsonschema.exceptions import ValidationError
        from openapi_schema_validator import OAS30Validator

        validator = OAS30Validator(
            expand_schema(self.doc, self.doc["components"]["schemas"]["ReviewQualityAssessmentRequest"])
        )
        request = {
            "assignment_id": "00000000-0000-4000-8000-000000000002",
            "revision_id": "00000000-0000-4000-8000-000000000003",
            "score": 70,
            "decision": "confirmed",
            "feedback": "Reasoned, useful feedback.",
        }
        validator.validate(request)
        validator.validate({**request, "decision": "needs_revision", "score": 80})
        with self.assertRaises(ValidationError):
            validator.validate({**request, "score": 69.99})

    def test_uncreated_reference_and_pending_progression_allow_null(self):
        from openapi_schema_validator import OAS30Validator

        for parent, field in [("AdminCase", "reference"), ("Feedback", "progression")]:
            with self.subTest(parent=parent, field=field):
                schema = self.doc["components"]["schemas"][parent]["properties"][field]
                OAS30Validator(expand_schema(self.doc, schema)).validate(None)


class AccessAndRetryExampleTests(unittest.TestCase):
    """Contract examples only: do not substitute for backend authorization/concurrency tests."""

    def test_directory_omitted_and_empty_are_root_but_content_requires_a_file(self):
        for raw in (None, ""):
            self.assertEqual(contract_path(raw, directory=True), "")
            with self.assertRaisesRegex(ValueError, "INVALID_PATH"):
                contract_path(raw)
        self.assertEqual(contract_path("src", directory=True, traversed_types=("dir",)), "src")
        with self.assertRaisesRegex(ValueError, "INVALID_PATH"):
            contract_path("src", traversed_types=("dir",))
        with self.assertRaisesRegex(ValueError, "INVALID_PATH"):
            contract_path("main.py", directory=True, traversed_types=("file",))

    def test_file_and_directory_paths_share_traversal_bytes_and_object_protections(self):
        for directory in (False, True):
            for raw in (
                "/etc",
                "../x",
                "a/../x",
                "a/./x",
                "a//b",
                "a/",
                ".GIT/config",
                "a\\b",
                "a%00b",
                "%2e%2e/x",
                "a%2f..%2fb",
                "%ff",
                "я" * 513,
            ):
                with self.subTest(directory=directory, raw=raw), self.assertRaisesRegex(ValueError, "INVALID_PATH"):
                    contract_path(raw, directory=directory)
            for kinds in (("symlink", "dir"), ("submodule", "file"), ("dir", "symlink")):
                with self.subTest(directory=directory, kinds=kinds), self.assertRaisesRegex(ValueError, "INVALID_PATH"):
                    contract_path("safe/child", directory=directory, traversed_types=kinds)
            self.assertEqual(contract_path("я" * 512, directory=directory), "я" * 512)

    def test_percent_decoding_occurs_once_not_once_per_layer(self):
        for directory in (False, True):
            self.assertEqual(contract_path("a%20b", directory=directory), "a b")
            self.assertEqual(contract_path("%252e%252e/x", directory=directory), "%2e%2e/x")
            self.assertEqual(contract_path("safe%252fchild", directory=directory), "safe%2fchild")

    def source_facts(self):
        return {
            "owned": True,
            "current_expert": True,
            "kind": "quality_check",
            "status": "assigned",
            "lease_live": True,
            "current_assignment": True,
            "pinned_revision_id": "r1",
            "current_revision_id": "r1",
            "review_status": "pending_quality_check",
        }

    def test_two_revisions_cannot_mix_pinned_and_current_content(self):
        facts = self.source_facts()
        r1 = {"id": "r1", "feedback": "first private feedback"}
        payload = {
            "revision": r1,
            "review": {"current_revision": r1, "submission_id": "s1"},
            "submission": {"id": "s1"},
        }
        contract_source_payload(facts, payload)
        for current in (
            {"id": "r2", "feedback": "different pending feedback"},
            {"id": "r1", "feedback": "mixed content"},
        ):
            with self.subTest(current=current), self.assertRaisesRegex(ValueError, "Mixed source revision"):
                contract_source_payload(
                    facts, {**payload, "review": {**payload["review"], "current_revision": current}}
                )
        facts["current_revision_id"] = "r2"
        self.assertEqual(contract_source_read(facts), (409, "STALE_REVISION"))
        with self.assertRaisesRegex(ValueError, "Source read denied"):
            contract_source_payload(facts, payload)

    def test_foreign_or_revoked_source_hides_stale_assignment_state(self):
        for changes in ({"owned": False}, {"current_expert": False}):
            facts = {**self.source_facts(), "current_revision_id": "r2", "status": "expired", **changes}
            self.assertEqual(contract_source_read(facts), (404, "NOT_FOUND"))

    def test_replaced_expired_cancelled_completed_source_assignments_fail_closed(self):
        for changes, code in (
            ({"current_assignment": False}, "STALE_REVISION"),
            ({"status": "expired"}, "ASSIGNMENT_EXPIRED"),
            ({"lease_live": False}, "ASSIGNMENT_EXPIRED"),
            ({"status": "cancelled"}, "ASSIGNMENT_EXPIRED"),
            ({"status": "completed"}, "ASSIGNMENT_NOT_ACTIVE"),
        ):
            with self.subTest(changes=changes):
                self.assertEqual(contract_source_read({**self.source_facts(), **changes}), (409, code))

    def test_active_equal_facts_precede_new_purchase_eligibility_and_changed_facts_conflict(self):
        for resource, state in (("priority", "reserved"), ("readiness", "pending")):
            with self.subTest(resource=resource):
                self.assertEqual(contract_active_attempt(resource, state, True, False), 200)
                self.assertEqual(contract_active_attempt(resource, state, False, True), 409)

    def test_terminal_attempts_need_distinct_eligible_new_creation(self):
        for resource, states in (
            ("priority", ("cancelled", "refunded")),
            ("readiness", ("needs_development", "approved")),
        ):
            for state in states:
                with self.subTest(resource=resource, state=state):
                    self.assertEqual(contract_active_attempt(resource, state, True, True), 201)
                    self.assertEqual(contract_active_attempt(resource, state, True, False), 409)
        self.assertEqual(contract_active_attempt("priority", "settled", True, True), 409)

    def test_readiness_self_read_does_not_require_expert_or_allow_self_assessment(self):
        facts = {"applicant": True, "competency_id": "sql", "skills": {}}
        self.assertEqual(contract_readiness_access(**facts, method="get"), 200)
        self.assertEqual(contract_readiness_access(**facts, method="post"), 403)
        self.assertEqual(contract_readiness_access(**facts, method="get", assessment_matches=False), 404)

    def test_readiness_requires_current_confirmed_same_skill_expert_for_reads_and_assessment(self):
        skill = {"grade": "expert", "grant": "confirmed", "expert_confirmed": True, "current": True}
        for method, success in (("get", 200), ("post", 201)):
            facts = {"applicant": False, "competency_id": "sql", "method": method}
            self.assertEqual(contract_readiness_access(**facts, skills={"sql": skill}), success)
            self.assertEqual(contract_readiness_access(**facts, skills={"api": skill}), 404)
            for change in (
                {"grant": "supervised"},
                {"expert_confirmed": False},
                {"current": False},
                {"grade": "review_plus", "value": 100},
            ):
                with self.subTest(method=method, change=change):
                    self.assertEqual(contract_readiness_access(**facts, skills={"sql": {**skill, **change}}), 404)


class PolicyExampleTests(unittest.TestCase):
    """Executable examples of the proposed contract, not production policy code."""

    def setUp(self):
        self.skills = [
            {"competency_id": "sql", "required_value": 80, "weight": 0.1},
            {"competency_id": "api", "required_value": 40, "weight": 0.25},
        ]
        self.reviewer = {
            "sql": {"value": 80, "grade": "expert", "grant": "confirmed", "expert_confirmed": True},
            "api": {"value": 40, "grade": "review_minus", "grant": "confirmed", "expert_confirmed": False},
        }

    def test_multi_skill_relevant_expert_passes(self):
        self.assertTrue(contract_eligible(self.skills, {}, self.reviewer))

    def test_sql_only_expert_cannot_review_whole_case(self):
        self.reviewer.pop("api")
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_unrelated_expert_and_high_value_reviewer_plus_rejected(self):
        self.reviewer["sql"].update(grade="review_plus", expert_confirmed=False)
        self.reviewer["api"].update(grade="expert", value=100, expert_confirmed=True)
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_value_75_is_not_expert_confirmation(self):
        self.skills[0]["required_value"] = 75
        self.reviewer["sql"].update(value=100, expert_confirmed=False)
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_supervised_expert_is_not_confirmed_expert(self):
        self.reviewer["sql"]["grant"] = "supervised"
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_case_threshold_50_requires_reviewer_plus(self):
        self.skills[1]["required_value"] = 50
        self.reviewer["api"]["value"] = 50
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_author_per_skill_floor_still_applies(self):
        self.assertFalse(contract_eligible(self.skills, {"api": "review_plus"}, self.reviewer))

    def test_author_floor_requires_independently_confirmed_expert(self):
        self.reviewer["api"].update(value=80, grade="expert", grant="supervised", expert_confirmed=False)
        for author_grade in ("review_plus", "expert"):
            with self.subTest(author_grade=author_grade):
                self.assertFalse(contract_eligible(self.skills, {"api": author_grade}, self.reviewer))
        self.reviewer["api"].update(grant="confirmed", expert_confirmed=True)
        self.assertTrue(contract_eligible(self.skills, {"api": "expert"}, self.reviewer))

    def test_unconfirmed_expert_is_not_a_qualified_higher_grade(self):
        self.reviewer["api"].update(value=80, grade="expert", grant="supervised", expert_confirmed=False)
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_weights_never_determine_complexity(self):
        self.skills[0]["weight"] = 0.0001
        self.reviewer["sql"].update(grade="review_plus", expert_confirmed=False)
        self.assertFalse(contract_eligible(self.skills, {}, self.reviewer))

    def test_duplicate_ids_with_different_weights_rejected(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_COMPETENCY"):
            validate_case_skills([self.skills[0], {**self.skills[0], "weight": 0.2}])

    def test_required_value_and_weight_boundaries(self):
        for field, value in [("required_value", -1), ("required_value", 101), ("weight", 0), ("weight", 0.25001)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_case_skills([{**self.skills[0], field: value}])
        validate_case_skills([{**self.skills[0], "required_value": 0, "weight": 0.25}])
        validate_case_skills([{**self.skills[0], "required_value": 100}])

    def test_exact_agreed_formula_example(self):
        self.assertEqual(Decimal(80) * Decimal("0.1") * (1 + Decimal("0.2") * Decimal(75) / 100), Decimal("9.2"))


if __name__ == "__main__":
    unittest.main()
