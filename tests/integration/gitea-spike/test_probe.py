"""Offline harness safety tests. Mock responses are NOT Gitea ACL evidence."""

import base64
import copy
import io
import json
import os
import signal
import socket
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import probe


def setUpModule():
    # A missed transport mock must fail offline, never contact any service.
    for attribute in ("create_connection", "getaddrinfo"):
        guard = patch.object(socket, attribute, side_effect=AssertionError("offline test attempted network"))
        guard.start()
        unittest.addModuleCleanup(guard.stop)


def fixture():
    # Synthetic identifiers/digest for validation tests, NOT an approved image.
    cfg = json.loads(Path(__file__).with_name("fixture.example.json").read_text())
    cfg.update(
        origin="https://gitea.invalid",
        release="1.99.0",
        image="registry.invalid/prodguild/gitea@sha256:" + "a" * 64,
        namespace="prodguild-t01-unittest",
    )
    for index, role in enumerate(probe.ROLES, 1):
        cfg["accounts"][role].update(username="probe-" + role, id=index)
    for index, repo in enumerate(probe.REPOS, 101):
        cfg["repositories"][repo].update(id=index, marker="PRODGUILD_T01_" + repo.upper() + "_UNITTEST")
    for role, character in (("student_a", "a"), ("student_b", "b")):
        cfg["solutions"][role].update(pull=1, review=2, submitted_sha=character * 40, working_sha="c" * 40)
    return cfg


def tokens():
    return {role: f"fake_{role}_unit_test_token_only" for role in probe.ROLES}


def response(data, status=200):
    return probe.Response(status, "application/json", json.dumps(data).encode())


def approved_args(cfg):
    args = ["--fixture", "not-read-in-unit-test", "--execute-read-only", "--approval-reference", "UNITTEST-ONLY"]
    for field in ("origin", "release", "image", "namespace"):
        args.extend(("--approve-" + field, cfg[field]))
    for role in probe.ROLES:
        args.extend(("--approve-account", f"{role}={cfg['accounts'][role]['username']}"))
        args.extend(("--approve-token-env", f"{role}={cfg['accounts'][role]['token_env']}"))
    return args


class FakeService:
    """Deterministic transport simulator. Never opens a socket."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.requests = []
        self.items = {item.path: item for item in probe.resources(cfg)}
        self.role_by_token = {value: role for role, value in tokens().items()}
        self.override = None

    def get(self, path, token):
        role = self.role_by_token[token]
        self.requests.append((role, path))
        if self.override:
            result = self.override(role, path)
            if result is not None:
                return result
        if path == "/api/v1/version":
            return response({"version": self.cfg["release"]})
        if path == "/api/v1/user":
            account = self.cfg["accounts"][role]
            return response({"id": account["id"], "login": account["username"], "is_admin": False})
        item = self.items[path]
        if role in probe.DENIED[item.repo]:
            return response({"message": "Not Found"}, 404)
        repo = self.cfg["repositories"][item.repo]
        marker = repo["marker"]
        if item.kind == "metadata":
            return response(
                {"id": repo["id"], "full_name": self.cfg["namespace"] + "/" + repo["name"], "private": True}
            )
        if item.kind == "refs":
            return response([{"ref": "refs/heads/main"}])
        if item.kind == "file":
            return response(
                {"type": "file", "encoding": "base64", "content": base64.b64encode(marker.encode()).decode()}
            )
        if item.kind == "pull":
            sol = self.cfg["solutions"]["student_" + item.repo[-1]]
            return response(
                {
                    "number": item.expected,
                    "body": marker,
                    "merged": False,
                    "head": {"sha": sol["submitted_sha"], "repo": {"id": repo["id"]}},
                    "base": {"repo": {"id": repo["id"]}},
                }
            )
        if item.kind == "diff":
            return probe.Response(200, "text/plain", ("diff --git a/probe.txt b/probe.txt\n+" + marker).encode())
        if item.kind in ("comments", "reviews"):
            comment = {"id": item.expected or 3, "body": marker}
            if item.kind == "reviews":
                comment["commit_id"] = self.cfg["solutions"]["student_" + item.repo[-1]]["submitted_sha"]
            return response([comment])
        if item.kind == "branch":
            return response({"commit": {"id": item.expected}})
        if item.kind == "commit":
            return response({"sha": item.expected})
        raise AssertionError("unknown mock surface")


class FixtureTests(unittest.TestCase):
    def test_example_is_non_runnable(self):
        with self.assertRaises(probe.Blocked):
            probe.load_fixture(Path(__file__).with_name("fixture.example.json"))

    def test_synthetic_fixture_validates(self):
        self.assertEqual(probe.validate_fixture(fixture())["schema_version"], 1)

    def test_unknown_secret_fields_rejected_at_each_level(self):
        for location in ((), ("accounts", "backend"), ("repositories", "starter"), ("solutions", "student_a")):
            cfg = fixture()
            target = cfg
            for key in location:
                target = target[key]
            target["token"] = "do-not-accept-credentials-in-json"
            with self.subTest(location=location), self.assertRaises(probe.Blocked):
                probe.validate_fixture(cfg)

    def test_missing_and_duplicate_accounts_ids_repositories_env(self):
        for field in ("username", "id", "token_env"):
            cfg = fixture()
            cfg["accounts"]["student_b"][field] = cfg["accounts"]["student_a"][field]
            with self.subTest(field=field), self.assertRaises(probe.Blocked):
                probe.validate_fixture(cfg)
        cfg = fixture()
        del cfg["accounts"]["expert"]
        with self.assertRaises(probe.Blocked):
            probe.validate_fixture(cfg)
        cfg = fixture()
        cfg["repositories"]["solution_b"]["id"] = cfg["repositories"]["solution_a"]["id"]
        with self.assertRaises(probe.Blocked):
            probe.validate_fixture(cfg)

    def test_unsafe_identifiers_tags_and_arbitrary_env_names(self):
        cases = [
            (("namespace",), "production"),
            (("image",), "registry.invalid/gitea:latest"),
            (("release",), "latest"),
            (("accounts", "backend", "token_env"), "AWS_SECRET_ACCESS_KEY"),
            (("accounts", "backend", "id"), True),
            (("repositories", "starter", "name"), "../production"),
            (("solutions", "student_a", "retained_branch"), "../main"),
        ]
        for location, value in cases:
            cfg = fixture()
            target = cfg
            for key in location[:-1]:
                target = target[key]
            target[location[-1]] = value
            with self.subTest(location=location), self.assertRaises(probe.Blocked):
                probe.validate_fixture(cfg)

    def test_same_branch_or_commit_rejected(self):
        for field, source in (("working_sha", "submitted_sha"), ("working_branch", "retained_branch")):
            cfg = fixture()
            sol = cfg["solutions"]["student_a"]
            sol[field] = sol[source]
            with self.assertRaises(probe.Blocked):
                probe.validate_fixture(cfg)

    def test_duplicate_json_and_fixture_size(self):
        with self.assertRaises(probe.Blocked):
            json.loads('{"origin":1,"origin":2}', object_pairs_hook=probe.unique_object)
        fake_path = Mock()
        fake_path.open.return_value = io.BytesIO(b" " * (probe.MAX_FIXTURE + 1))
        with self.assertRaisesRegex(probe.Blocked, "fixture_too_large"):
            probe.load_fixture(fake_path)


class ApprovalTests(unittest.TestCase):
    def test_explicit_complete_approval_required(self):
        cfg = fixture()
        args = probe.parser().parse_args(approved_args(cfg))
        probe.approve(cfg, args)
        for field in (
            "execute_read_only",
            "approval_reference",
            "approve_origin",
            "approve_release",
            "approve_image",
            "approve_namespace",
            "approve_account",
            "approve_token_env",
        ):
            invalid = copy.deepcopy(args)
            setattr(invalid, field, [] if field in ("approve_account", "approve_token_env") else None)
            with self.subTest(field=field), self.assertRaises(probe.Blocked):
                probe.approve(cfg, invalid)

    def test_duplicate_approval_does_not_replace_missing_role(self):
        cfg = fixture()
        args = probe.parser().parse_args(approved_args(cfg))
        args.approve_account[-1] = args.approve_account[0]
        with self.assertRaises(probe.Blocked):
            probe.approve(cfg, args)

    def test_changed_env_name_requires_its_own_explicit_approval(self):
        cfg = fixture()
        args = probe.parser().parse_args(approved_args(cfg))
        cfg["accounts"]["backend"]["token_env"] = "PRODGUILD_T01_ANOTHER_TOKEN"
        with self.assertRaisesRegex(probe.Blocked, "token_env_approval_mismatch"):
            probe.approve(cfg, args)

    def test_credentials_distinct_valid_and_only_named_env_read(self):
        cfg = fixture()
        env = {cfg["accounts"][role]["token_env"]: value for role, value in tokens().items()}
        env["UNRELATED_SECRET"] = "ignored"
        self.assertEqual(probe.credentials(cfg, env), tokens())
        for value in ("", "short", "x" * 257, "x" * 20 + "\n", env["PRODGUILD_T01_BACKEND_TOKEN"]):
            invalid = dict(env, PRODGUILD_T01_STUDENT_A_TOKEN=value)
            with self.subTest(value_length=len(value)), self.assertRaises(probe.Blocked):
                probe.credentials(cfg, invalid)

    def test_no_execution_means_no_env_discovery_or_client(self):
        cfg = fixture()
        with (
            patch.object(probe, "load_fixture", return_value=cfg),
            patch.object(probe, "credentials") as creds,
            patch.object(probe, "Client") as client,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(probe.main(["--fixture", "anything"]), 2)
        creds.assert_not_called()
        client.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["overall"], "blocked")


class HttpTests(unittest.TestCase):
    def test_url_safety(self):
        bad = (
            "http://gitea.invalid",
            "https://user:password@gitea.invalid",
            "https://user@gitea.invalid",
            "https://gitea.invalid/",
            "https://gitea.invalid/api",
            "https://gitea.invalid?token=secret",
            "https://gitea.invalid#fragment",
            "https://gitea.invalid?",
            "https://gitea.invalid#",
            "https://gitea.invalid:",
            "https://gitea.invalid:99999",
            "https://gitea.invalid\\evil",
            "https://gitea.invalid\n",
            "file:///etc/passwd",
            "https://%67itea.invalid",
            "https://[::1%25en0]",
        )
        for origin in bad:
            with self.subTest(origin=origin), self.assertRaises(probe.Blocked):
                probe.validate_origin(origin, False)

    def test_loopback_is_explicit_and_literal(self):
        for origin in ("http://127.0.0.1:3000", "http://[::1]:3000"):
            with self.assertRaises(probe.Blocked):
                probe.validate_origin(origin, False)
            self.assertEqual(probe.validate_origin(origin, True), origin)
        for origin in ("http://localhost:3000", "http://127.0.0.1.evil.invalid", "http://10.0.0.1"):
            with self.assertRaises(probe.Blocked):
                probe.validate_origin(origin, True)

    def test_redirect_never_followed_even_same_origin(self):
        for destination in ("https://gitea.invalid/login", "https://evil.invalid"):
            stream = Mock()
            with self.assertRaisesRegex(probe.Blocked, "redirect_refused"):
                probe.NoRedirect().redirect_request(None, stream, 302, "", {}, destination)
            stream.close.assert_called_once()

    def test_ambient_tls_key_logging_disabled_and_verification_required(self):
        with patch.dict(os.environ, {"SSLKEYLOGFILE": "must-not-open-this-path"}):
            client = probe.Client("https://gitea.invalid")
        handler = next(h for h in client.opener.handlers if isinstance(h, probe.HTTPSHandler))
        self.assertIsNone(handler._context.keylog_filename)
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, probe.ssl.CERT_REQUIRED)

    def fake_http(self, body=b"{}", status=200, headers=None):
        result = io.BytesIO(body)
        result.status = status
        result.headers = Message()
        result.headers["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            result.headers[key] = value
        return result

    def test_get_only_auth_header_no_proxy_and_bounded_timeout(self):
        client = probe.Client("https://gitea.invalid")
        with patch.object(client.opener, "open", return_value=self.fake_http()) as opened:
            client.get("/api/v1/user", tokens()["backend"])
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.full_url, "https://gitea.invalid/api/v1/user")
        self.assertEqual(request.get_header("Authorization"), "token " + tokens()["backend"])
        self.assertEqual(opened.call_args.kwargs["timeout"], probe.TIMEOUT)
        self.assertFalse(any(type(handler).__name__ == "ProxyHandler" for handler in client.opener.handlers))

    def test_body_length_stream_length_and_encoding_limits(self):
        for body, headers in (
            (b"12345", {}),
            (b"{}", {"Content-Length": "5"}),
            (b"{}", {"Content-Length": "invalid"}),
            (b"{}", {"Content-Encoding": "gzip"}),
        ):
            client = probe.Client("https://gitea.invalid", max_body=4)
            with (
                patch.object(client.opener, "open", return_value=self.fake_http(body, headers=headers)),
                self.assertRaises(probe.Blocked),
            ):
                client.get("/api/v1/user", tokens()["backend"])

    def test_duplicate_response_fields_are_not_positive_controls(self):
        with self.assertRaisesRegex(probe.Blocked, "response_duplicate_key"):
            probe.Response(200, "application/json", b'{"id":1,"id":2}').json()

    def test_redirect_http_error_timeout_transport_no_exception_disclosure(self):
        for error in (
            socket.timeout("secret-timeout"),
            URLError("secret-network-detail"),
            HTTPError("https://secret.invalid", 302, "secret-message", Message(), None),
        ):
            client = probe.Client("https://gitea.invalid")
            with patch.object(client.opener, "open", side_effect=error), self.assertRaises(probe.Blocked) as caught:
                client.get("/api/v1/user", tokens()["backend"])
            self.assertNotIn("secret", str(caught.exception))

    def test_hard_deadline_interrupts_slow_operation_without_network(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("Unix hard deadline unavailable; execution fails closed there")
        with self.assertRaisesRegex(probe.Blocked, "request_timeout"):
            with probe.deadline(0.02):
                time.sleep(0.2)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_absolute_cross_origin_path_and_header_injection_refused(self):
        client = probe.Client("https://gitea.invalid")
        for path in ("https://evil.invalid/api/v1/user", "//evil.invalid", "/api/v1/../user", "/api/v1/user\r\n"):
            with self.assertRaises(probe.Blocked):
                client.get(path, tokens()["backend"])
        with self.assertRaises(probe.Blocked):
            client.get("/api/v1/user", "x" * 20 + "\r\nAuthorization: evil")


class RunnerTests(unittest.TestCase):
    def run_service(self, customize=None):
        cfg = fixture()
        service = FakeService(cfg)
        if customize:
            customize(service)
        report = probe.Report()
        probe.Runner(cfg, tokens(), service, report).run()
        return report, service

    def test_simulated_success_is_still_blocked_manual_unrun(self):
        report, _ = self.run_service()
        self.assertTrue(
            all(
                row["status"] == "passed"
                for name, row in report.rows.items()
                if name.startswith(("deny.", "identity.", "pending.snapshot."))
            )
        )
        self.assertTrue(all(report.rows["manual." + name]["status"] == "manual-required" for name in probe.MANUAL))
        self.assertEqual(report.document()["overall"], "blocked")
        self.assertFalse(report.document()["isolation_proven"])
        self.assertEqual(report.exit_code(), 2)

    def test_version_mismatch_stops_before_identity(self):
        report, service = self.run_service(
            lambda s: setattr(
                s, "override", lambda role, path: response({"version": "0.0.0"}) if path == "/api/v1/version" else None
            )
        )
        self.assertEqual(report.rows["release.version"]["status"], "blocked")
        self.assertEqual(len(service.requests), 1)

    def test_wrong_identity_or_admin_cannot_support_denial(self):
        for fields in ({"id": 999}, {"login": "different"}, {"is_admin": True}, {"is_admin": None}):

            def customize(service):
                account = service.cfg["accounts"]["student_a"]
                data = dict(id=account["id"], login=account["username"], is_admin=False)
                data.update(fields)
                service.override = (
                    lambda role, path: response(data) if role == "student_a" and path == "/api/v1/user" else None
                )

            report, _ = self.run_service(customize)
            self.assertEqual(report.rows["identity.student_a"]["status"], "blocked")
            self.assertEqual(report.rows["deny.student_a.solution_b.metadata"]["status"], "blocked")

    def test_missing_or_public_allowed_repo_cannot_support_denial(self):
        for result in (response({"message": "missing"}, 404), response({"private": False})):

            def customize(service):
                path = next(i.path for i in service.items.values() if i.repo == "solution_a" and i.kind == "metadata")
                service.override = lambda role, p: result if role == "student_a" and p == path else None

            report, _ = self.run_service(customize)
            self.assertEqual(report.rows["control.private.student_a"]["status"], "blocked")
            self.assertEqual(report.rows["deny.student_a.solution_b.metadata"]["status"], "blocked")

    def test_401_500_redirect_and_html_are_not_denials(self):
        for result in (
            response({"message": "error"}, 401),
            response({"message": "error"}, 503),
            response({}, 302),
            probe.Response(403, "text/html", b"upstream unavailable"),
        ):

            def customize(service):
                path = next(i.path for i in service.items.values() if i.repo == "solution_b" and i.kind == "metadata")
                service.override = lambda role, p: result if role == "student_a" and p == path else None

            report, _ = self.run_service(customize)
            self.assertEqual(report.rows["deny.student_a.solution_b.metadata"]["status"], "blocked")

    def test_missing_resource_control_and_post_denial_outage_block(self):
        for after in (False, True):

            def customize(service):
                target = next(i.path for i in service.items.values() if i.repo == "solution_b" and i.kind == "diff")
                seen = 0

                def override(role, path):
                    nonlocal seen
                    if path == target and role == "student_b":
                        seen += 1
                        if not after or seen >= 3:
                            return response({"message": "outage"}, 503)
                    return None

                service.override = override

            report, _ = self.run_service(customize)
            self.assertEqual(report.rows["deny.student_a.solution_b.diff"]["status"], "blocked")

    def test_actor_identity_failure_after_denial_is_blocked(self):
        def customize(service):
            target = next(i.path for i in service.items.values() if i.repo == "reference" and i.kind == "metadata")
            denied = False

            def override(role, path):
                nonlocal denied
                if role == "student_a" and path == target:
                    denied = True
                if denied and role == "student_a" and path == "/api/v1/user":
                    return response({"message": "expired"}, 401)
                return None

            service.override = override

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["deny.student_a.reference.metadata"]["status"], "blocked")

    def test_retained_ref_wrong_commit_cannot_pass_snapshot(self):
        def customize(service):
            target = next(
                i.path for i in service.items.values() if i.repo == "solution_a" and i.surface == "retained_ref"
            )
            service.override = lambda role, path: response({"commit": {"id": "d" * 40}}) if path == target else None

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["control.resource.solution_a.retained_ref"]["status"], "blocked")
        self.assertEqual(report.rows["deny.student_b.solution_a.retained_ref"]["status"], "blocked")

    def test_wrong_pr_head_topology_or_merged_pr_cannot_pass(self):
        for change in ("head", "topology", "merged"):

            def customize(service):
                item = next(i for i in service.items.values() if i.repo == "solution_a" and i.kind == "pull")
                original = service.get(item.path, tokens()["student_a"]).json()
                if change == "head":
                    original["head"]["sha"] = "d" * 40
                elif change == "topology":
                    original["base"]["repo"]["id"] = 999
                else:
                    original["merged"] = True
                service.override = lambda role, path: response(original) if path == item.path else None

            report, _ = self.run_service(customize)
            self.assertEqual(report.rows["control.resource.solution_a.pull"]["status"], "blocked")

    def test_review_on_different_commit_cannot_pass(self):
        def customize(service):
            item = next(i for i in service.items.values() if i.repo == "solution_a" and i.kind == "reviews")
            original = service.get(item.path, tokens()["student_a"]).json()
            original[0]["commit_id"] = "d" * 40
            service.override = lambda role, path: response(original) if path == item.path else None

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["control.resource.solution_a.reviews"]["status"], "blocked")

    def test_successful_unauthorized_response_is_failed(self):
        def customize(service):
            target = next(i.path for i in service.items.values() if i.repo == "solution_b" and i.kind == "diff")
            service.override = (
                lambda role, path: response({"leak": "yes"}) if role == "student_a" and path == target else None
            )

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["deny.student_a.solution_b.diff"]["status"], "failed")
        self.assertEqual(report.exit_code(), 1)

    def test_private_marker_in_403_or_404_is_failed(self):
        for status in (403, 404):

            def customize(service):
                item = next(i for i in service.items.values() if i.repo == "reference" and i.kind == "metadata")
                marker = service.cfg["repositories"]["reference"]["marker"]
                service.override = (
                    lambda role, path: response({"message": "Denied", "private": marker + " private contents"}, status)
                    if role == "student_a" and path == item.path
                    else None
                )

            with self.subTest(status=status):
                report, _ = self.run_service(customize)
                row = report.rows["deny.student_a.reference.metadata"]
                self.assertEqual((row["status"], row["reason"]), ("failed", "private_marker_in_denial"))
                self.assertEqual(report.exit_code(), 1)
                self.assertNotIn("private contents", json.dumps(report.document()))

    def test_json_escaped_private_marker_in_denial_is_failed(self):
        for status in (403, 404):

            def customize(service):
                item = next(i for i in service.items.values() if i.repo == "reference" and i.kind == "metadata")
                marker = service.cfg["repositories"]["reference"]["marker"]
                raw = (
                    json.dumps({"message": "Denied", "private": marker})
                    .replace(marker, "".join("\\u%04x" % ord(c) for c in marker))
                    .encode()
                )
                service.override = (
                    lambda role, path: probe.Response(status, "application/json", raw)
                    if role == "student_a" and path == item.path
                    else None
                )

            with self.subTest(status=status):
                report, _ = self.run_service(customize)
                self.assertEqual(report.rows["deny.student_a.reference.metadata"]["status"], "failed")

    def test_decoded_nested_private_content_in_denial_is_failed(self):
        for status in (403, 404):

            def customize(service):
                item = next(i for i in service.items.values() if i.repo == "reference" and i.kind == "metadata")
                marker = service.cfg["repositories"]["reference"]["marker"]
                data = {
                    "message": "Denied",
                    "errors": [{"encoding": "base64", "content": base64.b64encode(marker.encode()).decode()}],
                }
                service.override = (
                    lambda role, path: response(data, status) if role == "student_a" and path == item.path else None
                )

            with self.subTest(status=status):
                report, _ = self.run_service(customize)
                row = report.rows["deny.student_a.reference.metadata"]
                self.assertEqual((row["status"], row["reason"]), ("failed", "private_marker_in_denial"))

    def test_complete_acl_markers_not_limited_to_automated_denial_rows(self):
        for repo in ("solution_a", "solution_b"):
            for status in (403, 404):

                def customize(service):
                    item = next(i for i in service.items.values() if i.repo == "pending" and i.kind == "metadata")
                    marker = service.cfg["repositories"][repo]["marker"]
                    service.override = (
                        lambda role, path: response({"message": "Denied", "private": marker}, status)
                        if role == "organizer" and path == item.path
                        else None
                    )

                with self.subTest(repo=repo, status=status):
                    report, _ = self.run_service(customize)
                    row = report.rows["deny.organizer.pending.metadata"]
                    self.assertEqual((row["status"], row["reason"]), ("failed", "private_marker_in_denial"))

    def test_pending_sentinel_control_required_before_absence(self):
        def customize(service):
            target = next(i.path for i in service.items.values() if i.repo == "pending" and i.kind == "file")
            service.override = lambda role, path: response({"message": "missing"}, 404) if path == target else None

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["pending.snapshot.solution_a.diff"]["status"], "blocked")

    def test_pending_plaintext_and_base64_leaks_detected(self):
        for kind in ("diff", "file"):

            def customize(service):
                sentinel = service.cfg["repositories"]["pending"]["marker"]
                target = next(
                    i
                    for i in service.items.values()
                    if i.repo == ("solution_a" if kind == "diff" else "starter") and i.kind == kind
                )
                marker = service.cfg["repositories"][target.repo]["marker"]
                result = (
                    probe.Response(200, "text/plain", ("diff --git a/x b/x\n+" + marker + sentinel).encode())
                    if kind == "diff"
                    else response(
                        {
                            "type": "file",
                            "encoding": "base64",
                            "content": base64.b64encode((marker + sentinel).encode()).decode(),
                        }
                    )
                )
                service.override = lambda role, path: result if path == target.path else None

            report, _ = self.run_service(customize)
            repo = "solution_a" if kind == "diff" else "starter"
            surface = "diff" if kind == "diff" else "probe_file"
            self.assertEqual(report.rows[f"pending.snapshot.{repo}.{surface}"]["status"], "failed")

    def test_json_escaped_pending_sentinel_is_detected(self):
        def customize(service):
            item = next(i for i in service.items.values() if i.repo == "solution_a" and i.kind == "comments")
            sentinel = service.cfg["repositories"]["pending"]["marker"]
            data = service.get(item.path, tokens()["student_a"]).json()
            data[0]["body"] += sentinel
            raw = json.dumps(data).replace(sentinel, "".join("\\u%04x" % ord(c) for c in sentinel)).encode()
            service.override = (
                lambda role, path: probe.Response(200, "application/json", raw) if path == item.path else None
            )

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["pending.snapshot.solution_a.comments"]["status"], "failed")

    def test_no_token_body_or_exception_text_in_json_output(self):
        cfg = fixture()
        secret = "arbitrary-sensitive-upstream-text"
        with (
            patch.object(probe, "load_fixture", return_value=cfg),
            patch.object(probe, "credentials", return_value=tokens()),
            patch.object(probe.Client, "get", side_effect=RuntimeError(secret + tokens()["backend"])),
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(probe.main(approved_args(cfg)), 2)
        self.assertEqual(errors.getvalue(), "")
        for value in (*tokens().values(), secret, cfg["origin"], cfg["image"]):
            self.assertNotIn(value, output.getvalue())
        json.loads(output.getvalue())

    def test_invalid_cli_does_not_echo_secret(self):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(probe.main(["--secret-token=never-echo-this"]), 2)
        self.assertNotIn("never-echo-this", output.getvalue() + errors.getvalue())

    def test_response_bodies_never_enter_evidence(self):
        secret = "synthetic-secret-never-publish-0123456789"

        def customize(service):
            target = next(i.path for i in service.items.values() if i.repo == "solution_b" and i.kind == "metadata")
            service.override = (
                lambda role, path: response({"message": secret}) if role == "student_a" and path == target else None
            )

        report, _ = self.run_service(customize)
        self.assertEqual(report.rows["deny.student_a.solution_b.metadata"]["status"], "failed")
        self.assertNotIn(secret, json.dumps(report.document()))


if __name__ == "__main__":
    unittest.main()
