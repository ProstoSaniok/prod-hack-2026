#!/usr/bin/env python3
"""Bounded, read-only, partial Gitea evidence collector; never an acceptance gate.

Only stdlib is used. No Git subprocesses, writes, redirects, proxies, or credential
discovery. Output deliberately contains no fixture values or upstream text.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

ROLES = ("backend", "organizer", "student_a", "student_b", "reviewer", "expert")
REPOS = ("starter", "reference", "draft_case", "solution_a", "solution_b", "pending")
DENIED = {
    "starter": (),
    "reference": ("student_a", "student_b", "reviewer", "expert"),
    "draft_case": ("student_a", "student_b", "reviewer", "expert"),
    "solution_a": ("student_b",),
    "solution_b": ("student_a",),
    "pending": ("organizer", "student_a", "student_b"),
}
# Full fixture confidentiality policy, separate from automated request coverage.
# Organizer's solution denials are manual, but their markers are still private.
ACL_DENIED = {
    **DENIED,
    "solution_a": ("organizer", "student_b"),
    "solution_b": ("organizer", "student_a"),
}
OWNER = {
    "starter": "backend",
    "reference": "organizer",
    "draft_case": "organizer",
    "solution_a": "student_a",
    "solution_b": "student_b",
    "pending": "backend",
}
CONTROL = {
    "backend": "pending",
    "organizer": "reference",
    "student_a": "solution_a",
    "student_b": "solution_b",
    "reviewer": "solution_a",
    "expert": "solution_a",
}
MANUAL = (
    "approved_image_support_availability",
    "token_scope_and_acl_inventory",
    "web_direct_links",
    "https_clone",
    "pending_all_git_objects",
    "protected_ref_api_and_git_writes",
    "retention_gc_restart",
    "private_fork_upstream_pr",
    "stale_blob_writes",
    "review_positions_fallback",
    "ambiguous_write_lookup",
    "bootstrap_identity_ssh",
    "no_solution_merge",
)
MAX_FIXTURE = 65_536
MAX_BODY = 262_144
TIMEOUT = 5.0
NAME = re.compile(r"[a-z][a-z0-9_-]{1,62}\Z")
MARKER = re.compile(r"PRODGUILD_T01_[A-Z0-9_]{8,96}\Z")
SHA = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")


class Blocked(Exception):
    """Only fixed, non-sensitive codes may be used as messages."""


def require(condition: bool, reason: str = "fixture_invalid") -> None:
    if not condition:
        raise Blocked(reason)


def exact(value: object, keys: set[str]) -> dict:
    require(type(value) is dict and set(value) == keys)
    return value


def positive_int(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_origin(origin: object, allow_loopback_http: bool) -> str:
    require(isinstance(origin, str) and origin.isascii(), "origin_invalid")
    require(not any(c.isspace() or ord(c) < 32 for c in origin), "origin_invalid")
    try:
        url = urlsplit(origin)
        port = url.port
        host = url.hostname
    except ValueError:
        raise Blocked("origin_invalid") from None
    require(bool(host) and url.username is None and url.password is None, "origin_invalid")
    require(not url.path and not url.query and not url.fragment, "origin_invalid")
    require("%" not in origin and "\\" not in origin, "origin_invalid")
    require(port is None or 1 <= port <= 65535, "origin_invalid")
    # Reject empty ports and alternate URL parsings, including empty ?/# suffixes.
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    require(origin == f"{url.scheme}://{authority}", "origin_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        require(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is not None, "origin_invalid")
        address = None
    if url.scheme != "https":
        require(
            url.scheme == "http" and allow_loopback_http and address is not None and address.is_loopback,
            "https_required",
        )
    return origin


def validate_fixture(value: object, allow_loopback_http: bool = False) -> dict:
    cfg = exact(
        value, {"schema_version", "origin", "release", "image", "namespace", "accounts", "repositories", "solutions"}
    )
    require(type(cfg["schema_version"]) is int and cfg["schema_version"] == 1)
    validate_origin(cfg["origin"], allow_loopback_http)
    require(
        isinstance(cfg["release"], str) and re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", cfg["release"]) is not None
    )
    require(
        isinstance(cfg["image"], str)
        and re.fullmatch(r"[a-z0-9][a-z0-9.:-]*/[a-z0-9][a-z0-9/_.-]*@sha256:[a-f0-9]{64}", cfg["image"]) is not None
    )
    require(
        isinstance(cfg["namespace"], str)
        and re.fullmatch(r"prodguild-t01-[a-z0-9]{8,24}", cfg["namespace"]) is not None
    )
    accounts = exact(cfg["accounts"], set(ROLES))
    for account in accounts.values():
        exact(account, {"username", "id", "token_env"})
        require(matches(NAME, account["username"]) and positive_int(account["id"]))
        require(
            isinstance(account["token_env"], str)
            and re.fullmatch(r"PRODGUILD_T01_[A-Z][A-Z0-9_]{1,63}_TOKEN", account["token_env"]) is not None
        )
    for field in ("username", "id", "token_env"):
        require(len({account[field] for account in accounts.values()}) == len(ROLES))
    repos = exact(cfg["repositories"], set(REPOS))
    for repo in repos.values():
        exact(repo, {"name", "id", "marker"})
        require(matches(NAME, repo["name"]) and positive_int(repo["id"]))
        require(matches(MARKER, repo["marker"]))
    for field in ("name", "id", "marker"):
        require(len({repo[field] for repo in repos.values()}) == len(REPOS))
    solutions = exact(cfg["solutions"], {"student_a", "student_b"})
    for solution in solutions.values():
        exact(solution, {"pull", "review", "submitted_sha", "working_sha", "working_branch", "retained_branch"})
        require(positive_int(solution["pull"]) and positive_int(solution["review"]))
        require(matches(SHA, solution["submitted_sha"]) and matches(SHA, solution["working_sha"]))
        require(solution["submitted_sha"] != solution["working_sha"])
        require(matches(NAME, solution["working_branch"]) and matches(NAME, solution["retained_branch"]))
        require(solution["working_branch"] != solution["retained_branch"])
    return cfg


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "fixture_duplicate_key")
        result[key] = value
    return result


def load_fixture(path: Path, allow_loopback_http: bool = False) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FIXTURE + 1)
        require(len(raw) <= MAX_FIXTURE, "fixture_too_large")
        cfg = json.loads(raw, object_pairs_hook=unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Blocked("fixture_unreadable") from None
    return validate_fixture(cfg, allow_loopback_http)


def approve(cfg: dict, args: argparse.Namespace) -> None:
    require(args.execute_read_only, "execution_not_authorized")
    require(
        isinstance(args.approval_reference, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,95}", args.approval_reference) is not None,
        "approval_required",
    )
    for field in ("origin", "release", "image", "namespace"):
        require(getattr(args, f"approve_{field}") == cfg[field], "approval_mismatch")
    expected = {f"{role}={cfg['accounts'][role]['username']}" for role in ROLES}
    require(
        len(args.approve_account) == len(ROLES) and set(args.approve_account) == expected, "account_approval_mismatch"
    )
    expected_env = {f"{role}={cfg['accounts'][role]['token_env']}" for role in ROLES}
    require(
        len(args.approve_token_env) == len(ROLES) and set(args.approve_token_env) == expected_env,
        "token_env_approval_mismatch",
    )
    require(
        hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread(),
        "hard_deadline_unavailable",
    )


def credentials(cfg: dict, environment: dict[str, str]) -> dict[str, str]:
    tokens = {}
    for role in ROLES:
        token = environment.get(cfg["accounts"][role]["token_env"], "")
        require(re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token) is not None, "credential_missing_or_invalid")
        tokens[role] = token
    require(len(set(tokens.values())) == len(ROLES), "credentials_not_distinct")
    return tokens


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    require(
        hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread(),
        "hard_deadline_unavailable",
    )
    # Do not interfere with another application's alarm when imported.
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "alarm_already_active")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum: int, _frame: object) -> None:
        raise Blocked("request_timeout")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise Blocked("redirect_refused")


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes

    def json(self) -> object:
        require(self.content_type == "application/json", "response_not_json")
        try:
            return json.loads(self.body, object_pairs_hook=unique_object)
        except Blocked:
            raise Blocked("response_duplicate_key") from None
        except (ValueError, UnicodeError, RecursionError):
            raise Blocked("response_invalid_json") from None


class Client:
    def __init__(
        self, origin: str, *, allow_loopback_http: bool = False, timeout: float = TIMEOUT, max_body: int = MAX_BODY
    ):
        self.origin = validate_origin(origin, allow_loopback_http)
        require(0 < timeout <= 10 and 0 < max_body <= MAX_BODY, "bounds_invalid")
        self.timeout = timeout
        self.max_body = max_body
        # Do not let SSLKEYLOGFILE cause ambient TLS key disclosure. Unlike
        # create_default_context(), this constructor does not enable key logging.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        # No environment proxy, cookie jar, netrc, Basic-auth discovery, or redirects.
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))

    def get(self, path: str, token: str) -> Response:
        require(
            re.fullmatch(r"/api/v1/[A-Za-z0-9_./?=&-]+", path) is not None and ".." not in path and "//" not in path,
            "request_path_invalid",
        )
        require(re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token) is not None, "credential_missing_or_invalid")
        request = Request(
            self.origin + path,
            method="GET",
            headers={
                "Authorization": "token " + token,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "prodguild-t01-read-only-probe/1",
            },
        )
        try:
            with deadline(self.timeout):
                try:
                    response = self.opener.open(request, timeout=self.timeout)
                except HTTPError as error:
                    response = error
                with response:
                    require(not 300 <= response.status < 400, "redirect_refused")
                    require(
                        response.headers.get("Content-Encoding", "identity").lower() == "identity",
                        "response_encoding_unsupported",
                    )
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        require(length.isdecimal() and int(length) <= self.max_body, "response_too_large")
                    body = response.read(self.max_body + 1)
                    require(len(body) <= self.max_body, "response_too_large")
                    return Response(response.status, response.headers.get_content_type(), body)
        except Blocked:
            raise
        except (TimeoutError, socket.timeout):
            raise Blocked("request_timeout") from None
        except (URLError, OSError, ssl.SSLError, ValueError):
            raise Blocked("transport_error") from None


@dataclass(frozen=True)
class Resource:
    repo: str
    surface: str
    path: str
    kind: str
    expected: object = None


def surfaces(repo: str) -> tuple[str, ...]:
    if repo.startswith("solution_"):
        return (
            "metadata",
            "refs",
            "pull",
            "diff",
            "comments",
            "reviews",
            "review_comments",
            "retained_ref",
            "working_ref",
            "commit",
        )
    return ("metadata", "refs", "probe_file")


def resources(cfg: dict) -> list[Resource]:
    result = []
    for repo in REPOS:
        base = f"/api/v1/repos/{cfg['namespace']}/{cfg['repositories'][repo]['name']}"
        result.extend(
            (Resource(repo, "metadata", base, "metadata"), Resource(repo, "refs", base + "/git/refs", "refs"))
        )
        if not repo.startswith("solution_"):
            result.append(Resource(repo, "probe_file", base + "/contents/probe.txt?ref=main", "file"))
            continue
        sol = cfg["solutions"]["student_" + repo[-1]]
        pull = f"{base}/pulls/{sol['pull']}"
        result.extend(
            (
                Resource(repo, "pull", pull, "pull", sol["pull"]),
                Resource(repo, "diff", pull + ".diff", "diff"),
                Resource(repo, "comments", f"{base}/issues/{sol['pull']}/comments?limit=50&page=1", "comments"),
                Resource(repo, "reviews", pull + "/reviews?limit=50&page=1", "reviews", sol["review"]),
                Resource(
                    repo, "review_comments", pull + f"/reviews/{sol['review']}/comments?limit=50&page=1", "comments"
                ),
                Resource(
                    repo, "retained_ref", base + f"/branches/{sol['retained_branch']}", "branch", sol["submitted_sha"]
                ),
                Resource(
                    repo, "working_ref", base + f"/branches/{sol['working_branch']}", "branch", sol["working_sha"]
                ),
                Resource(repo, "commit", base + f"/git/commits/{sol['submitted_sha']}", "commit", sol["submitted_sha"]),
            )
        )
    return result


def check_resource(resource: Resource, response: Response, cfg: dict) -> None:
    require(response.status == 200, "positive_control_not_200")
    repo = cfg["repositories"][resource.repo]
    marker = repo["marker"].encode("ascii")
    if resource.kind == "diff":
        require(response.body.startswith(b"diff --git ") and marker in response.body, "positive_diff_missing")
        return
    data = response.json()
    if resource.kind == "metadata":
        require(
            isinstance(data, dict)
            and positive_int(data.get("id"))
            and data["id"] == repo["id"]
            and data.get("full_name") == f"{cfg['namespace']}/{repo['name']}"
            and data.get("private") is True,
            "positive_private_repo_mismatch",
        )
    elif resource.kind == "refs":
        require(
            isinstance(data, list)
            and bool(data)
            and all(
                isinstance(ref, dict) and isinstance(ref.get("ref"), str) and ref["ref"].startswith("refs/")
                for ref in data
            ),
            "positive_refs_missing",
        )
    elif resource.kind == "file":
        require(
            isinstance(data, dict)
            and data.get("type") == "file"
            and data.get("encoding") == "base64"
            and isinstance(data.get("content"), str),
            "positive_file_missing",
        )
        try:
            content = base64.b64decode("".join(data["content"].split()), validate=True)
        except (ValueError, binascii.Error):
            raise Blocked("positive_file_invalid") from None
        require(marker in content, "positive_file_marker_missing")
    elif resource.kind == "pull":
        sol = cfg["solutions"]["student_" + resource.repo[-1]]
        require(
            isinstance(data, dict)
            and positive_int(data.get("number"))
            and data["number"] == resource.expected
            and marker in response.body,
            "positive_pull_missing",
        )
        require(
            data.get("merged") is False
            and isinstance(data.get("head"), dict)
            and data["head"].get("sha") == sol["submitted_sha"]
            and isinstance(data["head"].get("repo"), dict)
            and data["head"]["repo"].get("id") == repo["id"]
            and isinstance(data.get("base"), dict)
            and isinstance(data["base"].get("repo"), dict)
            and data["base"]["repo"].get("id") == repo["id"],
            "pull_commit_or_isolated_topology_mismatch",
        )
    elif resource.kind in ("comments", "reviews"):
        require(
            isinstance(data, list)
            and any(
                isinstance(item, dict)
                and positive_int(item.get("id"))
                and isinstance(item.get("body"), str)
                and repo["marker"] in item["body"]
                and (
                    resource.kind != "reviews"
                    or (
                        item["id"] == resource.expected
                        and item.get("commit_id") == cfg["solutions"]["student_" + resource.repo[-1]]["submitted_sha"]
                    )
                )
                for item in data
            ),
            "positive_comment_or_review_missing",
        )
    elif resource.kind == "branch":
        require(
            isinstance(data, dict)
            and isinstance(data.get("commit"), dict)
            and data["commit"].get("id") == resource.expected,
            "branch_snapshot_mismatch",
        )
    elif resource.kind == "commit":
        require(isinstance(data, dict) and data.get("sha") == resource.expected, "commit_snapshot_mismatch")


def marker_disclosed(response: Response, data: object, markers: tuple[bytes, ...]) -> bool:
    """Inspect bounded in-memory observations; never copy upstream data to evidence."""
    normalized = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if any(marker in body for marker in markers for body in (response.body, normalized)):
        return True
    # Error envelopes can include nested content payloads. Inspect every string,
    # including base64 content, rather than trusting the HTTP status/message key.
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            try:
                decoded = base64.b64decode("".join(value.split()), validate=True)
            except (ValueError, binascii.Error):
                continue
            if any(marker in decoded for marker in markers):
                return True
    return False


def row_ids() -> list[str]:
    ids = ["fixture.validation", "execution.approval", "release.version"]
    ids += [f"identity.{role}" for role in ROLES]
    ids += [f"control.private.{role}" for role in ROLES]
    for repo in REPOS:
        for surface in surfaces(repo):
            ids.append(f"control.resource.{repo}.{surface}")
            ids += [f"deny.{role}.{repo}.{surface}" for role in DENIED[repo]]
            if repo in ("starter", "solution_a", "solution_b"):
                ids.append(f"pending.snapshot.{repo}.{surface}")
    return ids + [f"manual.{name}" for name in MANUAL]


class Report:
    def __init__(self):
        self.fixture_sha256: str | None = None
        self.rows = {name: {"id": name, "status": "blocked", "reason": "not_run"} for name in row_ids()}
        for name in MANUAL:
            self.set(f"manual.{name}", "manual-required", "separate_authorization_and_evidence_required")

    def set(self, name: str, status: str, reason: str) -> None:
        require(name in self.rows and status in ("passed", "failed", "blocked", "manual-required"), "report_invalid")
        self.rows[name] = {"id": name, "status": status, "reason": reason}

    def document(self) -> dict:
        return {
            "schema_version": 1,
            "mode": "read-only-http-partial",
            "overall": "blocked",
            "isolation_proven": False,
            "fixture_sha256": self.fixture_sha256,
            "rows": list(self.rows.values()),
        }

    def exit_code(self) -> int:
        return 1 if any(row["status"] == "failed" for row in self.rows.values()) else 2


class Runner:
    def __init__(self, cfg: dict, tokens: dict[str, str], client: Client, report: Report):
        self.cfg, self.tokens, self.client, self.report = cfg, tokens, client, report
        self.items = resources(cfg)
        self.metadata = {item.repo: item for item in self.items if item.kind == "metadata"}
        self.ready: set[str] = set()

    def get(self, role: str, path: str) -> Response:
        return self.client.get(path, self.tokens[role])

    def identity(self, role: str) -> None:
        response = self.get(role, "/api/v1/user")
        require(response.status == 200, "identity_not_200")
        data = response.json()
        expected = self.cfg["accounts"][role]
        require(
            isinstance(data, dict)
            and positive_int(data.get("id"))
            and data.get("login") == expected["username"]
            and data.get("id") == expected["id"]
            and data.get("is_admin") is False,
            "identity_mismatch_or_admin",
        )

    def private_control(self, role: str) -> None:
        item = self.metadata[CONTROL[role]]
        check_resource(item, self.get(role, item.path), self.cfg)

    def evaluate(self, name: str, action: Callable[[], None], success: str) -> bool:
        try:
            action()
        except Blocked as error:
            self.report.set(name, "blocked", str(error))
            return False
        self.report.set(name, "passed", success)
        return True

    def version(self) -> None:
        response = self.get("backend", "/api/v1/version")
        require(response.status == 200, "version_not_200")
        data = response.json()
        require(isinstance(data, dict) and data.get("version") == self.cfg["release"], "release_mismatch")

    def deny(self, item: Resource, role: str) -> None:
        name = f"deny.{role}.{item.repo}.{item.surface}"
        owner = OWNER[item.repo]
        if role not in self.ready or owner not in self.ready:
            self.report.set(name, "blocked", "identity_or_private_control_unavailable")
            return
        try:
            # Bracket each denial with the SAME real resource and actor controls.
            self.identity(role)
            self.private_control(role)
            check_resource(item, self.get(owner, item.path), self.cfg)
            response = self.get(role, item.path)
            if 200 <= response.status < 300:
                self.report.set(name, "failed", "unauthorized_resource_accessible")
                return
            markers = tuple(
                self.cfg["repositories"][repo]["marker"].encode("ascii") for repo in REPOS if role in ACL_DENIED[repo]
            )
            if any(marker in response.body for marker in markers):
                self.report.set(name, "failed", "private_marker_in_denial")
                return
            require(response.status in (403, 404), "not_an_authenticated_denial")
            error = response.json()
            if marker_disclosed(response, error, markers):
                self.report.set(name, "failed", "private_marker_in_denial")
                return
            require(isinstance(error, dict) and isinstance(error.get("message"), str), "denial_body_unrecognized")
            self.identity(role)
            self.private_control(role)
            check_resource(item, self.get(owner, item.path), self.cfg)
        except Blocked as error:
            self.report.set(name, "blocked", str(error))
            return
        self.report.set(name, "passed", "api_denial_with_live_positive_controls_only")

    def run(self) -> None:
        if not self.evaluate("release.version", self.version, "exact_api_release_matched_not_image_attestation"):
            return
        for role in ROLES:
            if self.evaluate(f"identity.{role}", lambda: self.identity(role), "separate_non_admin_identity_matched"):
                if self.evaluate(
                    f"control.private.{role}", lambda: self.private_control(role), "allowed_private_repository_matched"
                ):
                    self.ready.add(role)
        pending = next(item for item in self.items if item.repo == "pending" and item.kind == "file")
        sentinel_confirmed = "backend" in self.ready and self.evaluate(
            "control.resource.pending.probe_file",
            lambda: check_resource(pending, self.get("backend", pending.path), self.cfg),
            "pending_sentinel_exists_in_private_control",
        )
        for item in self.items:
            owner = OWNER[item.repo]
            positive_name = f"control.resource.{item.repo}.{item.surface}"
            if owner not in self.ready:
                self.report.set(positive_name, "blocked", "owner_positive_control_unavailable")
                continue
            try:
                response = self.get(owner, item.path)
                check_resource(item, response, self.cfg)
            except Blocked as error:
                self.report.set(positive_name, "blocked", str(error))
                continue
            self.report.set(positive_name, "passed", "seeded_resource_snapshot_matched")
            if item.repo in ("starter", "solution_a", "solution_b"):
                # These owners are the intended student except starter's backend.
                # Starter is scanned again with both students, not just the backend.
                name = f"pending.snapshot.{item.repo}.{item.surface}"
                try:
                    require(sentinel_confirmed, "pending_sentinel_control_unavailable")
                    observations = [response]
                    if item.repo == "starter":
                        for role in ("student_a", "student_b"):
                            require(role in self.ready, "student_positive_control_unavailable")
                            student_response = self.get(role, item.path)
                            check_resource(item, student_response, self.cfg)
                            observations.append(student_response)
                    sentinel = self.cfg["repositories"]["pending"]["marker"].encode("ascii")
                    bodies = [observation.body for observation in observations]
                    if item.kind != "diff":
                        # Normalize JSON escapes before looking for the ASCII sentinel.
                        bodies += [
                            json.dumps(observation.json(), sort_keys=True).encode("ascii")
                            for observation in observations
                        ]
                    # Content API bodies contain base64, not plaintext.
                    if item.kind == "file":
                        bodies += [
                            base64.b64decode("".join(observation.json()["content"].split()), validate=True)
                            for observation in observations
                        ]
                    leaked = any(sentinel in body for body in bodies)
                    self.report.set(
                        name,
                        "failed" if leaked else "passed",
                        "pending_sentinel_visible" if leaked else "bounded_snapshot_only_not_git_history_proof",
                    )
                except Blocked as error:
                    self.report.set(name, "blocked", str(error))
            for role in DENIED[item.repo]:
                self.deny(item, role)


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Blocked("arguments_invalid")


def parser() -> argparse.ArgumentParser:
    result = SafeParser(description=__doc__)
    result.add_argument("--fixture", type=Path, required=True)
    result.add_argument("--execute-read-only", action="store_true")
    result.add_argument("--approval-reference")
    for field in ("origin", "release", "image", "namespace"):
        result.add_argument(f"--approve-{field}")
    result.add_argument("--approve-account", action="append", default=[], metavar="ROLE=USERNAME")
    result.add_argument("--approve-token-env", action="append", default=[], metavar="ROLE=ENV_NAME")
    result.add_argument("--allow-loopback-http", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    report = Report()
    try:
        args = parser().parse_args(argv)
        cfg = load_fixture(args.fixture, args.allow_loopback_http)
        report.fixture_sha256 = hashlib.sha256(
            json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        report.set("fixture.validation", "passed", "strict_fixture_validated")
        approve(cfg, args)
        tokens = credentials(cfg, os.environ)
        report.set("execution.approval", "passed", "explicit_read_only_fixture_approval_matched")
        Runner(cfg, tokens, Client(cfg["origin"], allow_loopback_http=args.allow_loopback_http), report).run()
    except Blocked as error:
        name = "execution.approval" if report.rows["fixture.validation"]["status"] == "passed" else "fixture.validation"
        report.set(name, "blocked", str(error))
    except (Exception, KeyboardInterrupt):
        # Never print exception text: upstream objects can contain credentials.
        report.set("execution.approval", "blocked", "probe_interrupted_or_internal_error")
    print(json.dumps(report.document(), indent=2))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
