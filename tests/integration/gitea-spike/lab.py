#!/usr/bin/env python3
"""Explicitly authorized, disposable LOCAL Gitea lab. Never production deployment.

All mutable state/secrets are in an ignored mode-0700 directory, files mode0600.
Only this run's exact labelled Docker resources may be restarted or removed.
The public evidence output is an allowlisted observation document, not raw I/O.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.client
import http.cookiejar
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

ROOT = Path(__file__).resolve().parents[3]
LABEL = "org.prodguild.t01.lab"
ROLES = ("backend", "organizer", "student_a", "student_b", "reviewer", "expert")
API = "/api/v1"
BODY_LIMIT = 2 * 1024 * 1024
# The local fixture's complete confidentiality policy (cf. probe.ACL_DENIED),
# independent of which endpoints a particular observation happens to request.
ACL_DENIED = {
    "starter": (),
    "reference": ("student_a", "student_b", "reviewer", "expert"),
    "draft_case": ("student_a", "student_b", "reviewer", "expert"),
    "solution_a": ("student_b", "organizer"),
    "solution_b": ("student_a", "organizer"),
    "pending": ("student_a", "student_b", "organizer", "reviewer", "expert"),
}
SCAN_DEPTH = 24
SCAN_NODES = 16384
SCAN_BYTES = 4 * BODY_LIMIT
SCAN_BASE64_DEPTH = 4
NATIVE_DENIAL_POLICY = "prodguild-t01-gitea28.1.0-exact-native-denial-v1"
MARKER_POLICY = "prodguild-t01-actor-forbidden-body-canaries-v2"


class LabError(Exception):
    pass


def need(condition, code):
    if not condition:
        raise LabError(code)


def private_write(path, value):
    path = Path(path)
    need(not path.is_symlink(), "private_file_symlink_refused")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    data = value if isinstance(value, bytes) else (json.dumps(value, indent=2) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise LabError("private_file_open_failed") from None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    os.chmod(path, 0o600)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def native_denial(result, url, *, operation, destination=None, branch=None, source=None, force=False):
    """Closed diagnostic grammar for the 28.1.0 lab's C-locale HTTP Git calls.

    A status/keyword somewhere in stderr is not evidence. Match the credential-free
    repository and (for hooks) destination ref, and reject every unrecognized line.
    Return only a fixed public classification; never upstream diagnostic text.
    """
    if result.returncode not in (1, 128) or result.stdout or len(result.stderr) > 65536:
        return None
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/[a-z0-9-]+/[a-z0-9-]+\.git", url):
        return None
    try:
        lines = result.stderr.decode("ascii").splitlines()
    except UnicodeError:
        return None
    lines = [line.rstrip() for line in lines if line.strip()]
    if operation == "clone":
        preamble = f"Cloning into bare repository '{destination}'..."
        if lines and lines[0] == preamble:
            lines.pop(0)
    if operation in ("clone", "fetch", "write") and result.returncode == 128:
        for status in (403,) if operation == "write" else (403, 404):
            fatal = f"fatal: unable to access '{url}/': The requested URL returned error: {status}"
            remotes = (
                ("remote: User permission denied for writing.",)
                if operation == "write"
                else ("remote: Repository not found.",)
            )
            if lines == [fatal] or any(lines == [remote, fatal] for remote in remotes):
                return "git-http-" + str(status) + "-exact-repository"
        if operation != "write" and lines in (
            [f"fatal: repository '{url}/' not found"],
            ["remote: Repository not found.", f"fatal: repository '{url}/' not found"],
            ["remote: Repository not found", f"fatal: repository '{url}/' not found"],
        ):
            return "git-repository-not-found-exact-repository"
    # Observed official28.1.0 hook framing. Match complete C-locale lines,
    # including the actual source/destination and exact repository footer.
    hook_lines = [line for line in lines if line not in ("remote:", "remote: error:")]
    if operation == "write" and result.returncode == 1 and branch and source:
        if hook_lines == [
            "remote: error: User permission denied for writing.",
            f"To {url}",
            f" ! [remote rejected] {source} -> {branch} (pre-receive hook declined)",
            f"error: failed to push some refs to '{url}'",
        ]:
            return "gitea-write-acl-exact-repository-ref"
    if operation != "protected" or result.returncode != 1 or not branch or source is None:
        return None
    # Gitea's hook emits one of these operation-specific messages. The matching
    # Git rejection/footer are mandatory; auth failures and generic hook failures
    # cannot establish protected-ref enforcement. Unknown output fails closed.
    reason = (
        f"branch {branch} is protected from deletion"
        if source == ""
        else f"Not allowed to push to protected branch {branch}"
    )
    message = "remote: Gitea: " + reason
    rejection = f" ! [remote rejected] {source or '(delete)'} -> {branch} (pre-receive hook declined)"
    footer = [f"To {url}", rejection, f"error: failed to push some refs to '{url}'"]
    if [line for line in lines if line != "remote:"] == [message, *footer]:
        return "gitea-protected-delete" if source == "" else "gitea-protected-push"
    observed_reason = f"Branch {branch} is protected from deletion" if source == "" else reason
    observed_rejection = f" ! [remote rejected] {branch} (pre-receive hook declined)" if source == "" else rejection
    if hook_lines == [
        "remote: error: " + observed_reason,
        f"To {url}",
        observed_rejection,
        f"error: failed to push some refs to '{url}'",
    ]:
        return "gitea-protected-delete" if source == "" else "gitea-protected-push"
    if (
        force
        and source
        and hook_lines
        == [
            f"remote: error: Branch {branch} is protected from force push",
            *footer,
        ]
    ):
        return "gitea-protected-push"
    return None


def scan_markers(raw, markers, code, *, require_json=False):
    """Inspect bounded raw/Unicode text, JSON, line fragments and nested base64.

    This is an in-memory canary check, not a universal content/encoding scanner.
    Ambiguous JSON and exhausted structure/decoding budgets fail closed.
    """
    need(len(raw) <= BODY_LIMIT, "marker_scan_body_limit")
    nodes, byte_count = 0, 0

    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, "marker_scan_ambiguous_json")
            value[key] = item
        return value

    def invalid_constant(value):
        raise LabError("marker_scan_ambiguous_json")

    def unicode_encoding(data):
        # The same UTF encodings JSON permits, including BOMless wide text.
        if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            return "utf-32"
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "utf-16"
        if data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if len(data) >= 4:
            if data[:3] == b"\x00\x00\x00":
                return "utf-32-be"
            if data[1:4] == b"\x00\x00\x00":
                return "utf-32-le"
            if data[0] == data[2] == 0:
                return "utf-16-be"
            if data[1] == data[3] == 0:
                return "utf-16-le"
        return None

    pending = [(raw, 0, 0, require_json, False)]

    def enqueue(values, depth, encodings, *, must_parse=False, explicit_base64=False):
        need(
            nodes + len(pending) + len(values) <= SCAN_NODES,
            "marker_scan_structure_limit",
        )
        pending.extend((value, depth + 1, encodings, must_parse, explicit_base64) for value in values)

    while pending:
        value, depth, encodings, must_parse, explicit_base64 = pending.pop()
        nodes += 1
        need(nodes <= SCAN_NODES and depth <= SCAN_DEPTH, "marker_scan_structure_limit")
        if isinstance(value, dict):
            if "encoding" in value:
                need(value["encoding"] == "base64", "marker_scan_unsupported_encoding")
                need(isinstance(value.get("content"), str), "marker_scan_invalid_base64")
                enqueue([value["content"]], depth, encodings, explicit_base64=True)
            children = [part for pair in value.items() for part in pair]
        elif isinstance(value, list):
            children = value
        else:
            children = None
        if children is not None:
            enqueue(children, depth, encodings)
            continue
        if not isinstance(value, (bytes, str)):
            continue
        try:
            data = value.encode("utf-8") if isinstance(value, str) else value
        except UnicodeError:
            raise LabError("marker_scan_invalid_text") from None
        byte_count += len(data)
        need(byte_count <= SCAN_BYTES, "marker_scan_byte_limit")
        need(not any(marker in data for marker in markers), code)
        stripped = data.strip()
        encoding = unicode_encoding(data)
        if encoding and not explicit_base64:
            try:
                text = data.decode(encoding)
            except UnicodeError:
                raise LabError("marker_scan_invalid_text") from None
            enqueue([text], depth, encodings, must_parse=must_parse)
            continue
        # Parse nested JSON strings/envelopes as well as top-level API JSON. Do
        # not silently downgrade a malformed JSON-looking body to ordinary text.
        if not explicit_base64 and (must_parse or stripped[:1] in (b"{", b"[", b'"')):
            try:
                parsed = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise LabError("marker_scan_invalid_json") from None
            enqueue([parsed], depth, encodings)
            continue
        if not explicit_base64:
            # A diff is a framing format, not a single base64/JSON value. Inspect
            # each bounded line and its added/deleted payload. Multiline partial
            # JSON fragments fail closed rather than claiming to reconstruct them.
            lines = data.splitlines()
            if len(lines) > 1:
                # JSON content values may be line-wrapped base64, not diff text.
                # Try that whole representation before inspecting line payloads.
                compact = b"".join(data.split())
                try:
                    decoded = base64.b64decode(compact + b"=" * (-len(compact) % 4), validate=True)
                except (ValueError, binascii.Error):
                    decoded = None
                if decoded is not None and compact in (
                    base64.b64encode(decoded),
                    base64.b64encode(decoded).rstrip(b"="),
                ):
                    need(encodings < SCAN_BASE64_DEPTH, "marker_scan_encoding_limit")
                    enqueue([decoded], depth, encodings + 1)
                    continue
                enqueue(lines, depth, encodings)
                continue
            if stripped[:1] in (b"+", b"-") and not stripped.startswith((b"+++ ", b"--- ")):
                enqueue([stripped[1:]], depth, encodings)
                continue
            if b"\\u" in data:
                try:
                    normalized = re.sub(
                        rb"(?:\\u[0-9a-fA-F]{4})+",
                        lambda match: json.loads(b'"' + match[0] + b'"').encode("utf-8"),
                        data,
                    )
                except (ValueError, UnicodeError):
                    raise LabError("marker_scan_invalid_escape") from None
                need(
                    normalized != data and b"\\u" not in normalized,
                    "marker_scan_invalid_escape",
                )
                enqueue([normalized], depth, encodings)
                continue
            need(b"\\x" not in data, "marker_scan_unsupported_escape")
        compact = b"".join(data.split())
        if not compact:
            continue
        if not explicit_base64 and re.fullmatch(rb"(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})", compact):
            # Literal fixture object IDs/hashes are not declared base64. Direct
            # markers were already checked; none of this lab's canary encodings
            # has an all-hex alphabet. Avoid interpreting random hash bytes as JSON.
            continue
        # Missing padding is supported for declared content and long implicit
        # candidates; short ordinary words are not evidence of an encoding.
        padded = compact
        if len(compact) % 4 and (explicit_base64 or (len(compact) >= 16 and compact == stripped)):
            padded += b"=" * (-len(compact) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True)
        except (ValueError, binascii.Error):
            need(not explicit_base64, "marker_scan_invalid_base64")
            continue
        need(encodings < SCAN_BASE64_DEPTH, "marker_scan_encoding_limit")
        canonical = base64.b64encode(decoded)
        if compact not in (canonical, canonical.rstrip(b"=")):
            # A literal slash-containing branch name is not a declared encoding.
            # Still reject leaks/structured encodings rather than trusting it.
            need(not any(marker in decoded for marker in markers), code)
            need(
                not explicit_base64
                and b"=" not in compact
                and len(compact) >= 16
                and not unicode_encoding(decoded)
                and decoded.lstrip()[:1] not in (b"{", b"[", b'"'),
                "marker_scan_ambiguous_base64",
            )
            enqueue([decoded], depth, encodings + 1)
            continue
        enqueue([decoded], depth, encodings + 1)


def process(args, *, data=None, env=None, cwd=None, timeout=90, allowed=(0,)):
    try:
        result = subprocess.run(
            args,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise LabError("bounded_process_timeout") from None
    except OSError:
        raise LabError("subprocess_launch_failed") from None
    need(result.returncode in allowed, "subprocess_failed_" + Path(args[0]).name)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Form(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "_csrf":
            self.csrf = values.get("value")


class Lab:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        need(
            self.directory.is_relative_to((ROOT / "artifacts").resolve()),
            "state_must_be_ignored_artifacts",
        )
        self.state_file = self.directory / "fixture.local.json"
        need(not self.state_file.is_symlink(), "fixture_symlink_refused")
        if self.state_file.exists():
            need(
                self.state_file.stat().st_mode & 0o777 == 0o600,
                "unsafe_fixture_permissions",
            )
        self.cfg = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.rows = []
        self.cookies = {}
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def save(self):
        private_write(self.state_file, self.cfg)

    def docker(self, *args, data=None, timeout=90, allowed=(0,)):
        # A host-loopback binding on a remote daemon would violate local-only
        # authorization. Resolve once, require a local Unix socket, then pin it.
        need(
            not any(
                os.environ.get(key)
                for key in (
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                    "DOCKER_TLS_VERIFY",
                    "DOCKER_CERT_PATH",
                )
            ),
            "docker_routing_override_not_allowed",
        )
        env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        endpoint = self.cfg.get("docker_endpoint")
        if not endpoint:
            contexts = json.loads(process(["docker", "context", "inspect"], env=env).stdout)
            endpoint = contexts[0]["Endpoints"]["docker"]["Host"]
        need(endpoint.startswith("unix:///"), "docker_daemon_must_be_local_unix_socket")
        need(
            stat.S_ISSOCK(Path(endpoint[7:]).stat().st_mode),
            "docker_endpoint_not_local_socket",
        )
        self.cfg["docker_endpoint"] = endpoint
        self.save()
        return process(
            ["docker", "--host", endpoint, *args],
            data=data,
            env=env,
            timeout=timeout,
            allowed=allowed,
        )

    def owned(self, kind, identifier):
        raw = self.docker(kind, "inspect", identifier).stdout
        value = json.loads(raw)[0]
        labels = value.get("Labels") if kind in ("volume", "network") else value["Config"].get("Labels")
        need(labels and labels.get(LABEL) == self.cfg["run_id"], "resource_not_owned")
        return value

    @property
    def origin(self):
        return self.valid_origin(self.cfg["origin"])

    @staticmethod
    def valid_origin(origin):
        match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", origin)
        need(match and 1 <= int(match.group(1)) <= 65535, "lab_requires_literal_loopback")
        return origin

    def http(
        self,
        method,
        path,
        *,
        role=None,
        body=None,
        expected=(200,),
        headers=None,
        cookie=None,
        basic=None,
        origin=None,
        response_role="request",
    ):
        if response_role == "request":
            need(cookie is None and basic is None, "explicit_response_actor_required")
            response_role = role
        need(role is None or role == response_role, "response_actor_mismatch")
        decoded = unquote(path)
        need(
            path.startswith("/")
            and not decoded.startswith("//")
            and "\\" not in decoded
            and "#" not in path
            and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            and ".." not in decoded.split("?", 1)[0].split("/"),
            "unsafe_request_path",
        )
        base = self.valid_origin(origin) if origin is not None else self.origin
        outgoing = {"Accept-Encoding": "identity", **(headers or {})}
        if role:
            outgoing["Authorization"] = "token " + self.cfg["accounts"][role]["token"]
        if basic:
            outgoing["Authorization"] = "Basic " + base64.b64encode((basic[0] + ":" + basic[1]).encode()).decode()
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            outgoing["Content-Type"] = "application/json"
        request = Request(base + path, method=method, data=body, headers=outgoing)
        opener = cookie or self.opener
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            data = response.read(BODY_LIMIT + 1)
            need(len(data) <= BODY_LIMIT, "response_body_limit")
            status = response.status
            response_headers = dict(response.headers)
        self.inspect_response(data, response_role, path, headers=response_headers)
        if status not in expected:
            private_write(
                self.directory / "last-error.local.json",
                {
                    "method": method,
                    "path": path,
                    "status": status,
                    "body": data.decode(errors="replace"),
                },
            )
            raise LabError("unexpected_http_" + str(status))
        return status, data, response_headers

    def api(self, method, path, *, role="backend", body=None, expected=(200,), headers=None):
        status, raw, response_headers = self.http(
            method, API + path, role=role, body=body, expected=expected, headers=headers
        )
        # Keep the parsing boundary guarded even for alternate/in-memory HTTP
        # transports. In particular no mutation denial may discard its body.
        self.inspect_response(raw, role, API + path, headers=response_headers)
        return status, json.loads(raw) if raw else None, response_headers

    def readiness(self):
        end = time.monotonic() + 90
        while time.monotonic() < end:
            try:
                if "backend" not in self.cfg.get("accounts", {}):
                    status, _, _ = self.http("GET", "/user/login")
                    if status == 200:
                        return
                status, raw, _ = self.http("GET", API + "/version", role="backend")
                if status == 200 and json.loads(raw)["version"] == self.cfg["release"]:
                    return
            except (OSError, URLError, LabError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        raise LabError("startup_timeout")

    def bootstrap(self, lock_file):
        need(not self.cfg and not self.state_file.exists(), "fixture_already_exists")
        lock = json.loads(Path(lock_file).read_text())
        need(
            lock["local_only"] is True and lock["source_repository"] == "go-gitea/gitea",
            "release_lock_invalid",
        )
        image = lock["image"]
        need(
            re.fullmatch(r"docker\.gitea\.com/gitea@sha256:[0-9a-f]{64}", image),
            "official_digest_required",
        )
        need(
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", lock["release"]),
            "pinned_release_required",
        )
        nonce = secrets.token_hex(5)
        run_id = "prodguild-t01-lab-" + nonce
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.cfg = {
            "run_id": run_id,
            "namespace": "prodguild-t01-lab" + nonce,
            "release": lock["release"],
            "image": image,
            "image_tag": lock["tag"],
            "origin": f"http://127.0.0.1:{port}",
            "container": run_id,
            "volume": run_id + "-data",
            "network": run_id + "-net",
            "accounts": {},
            "repos": {},
        }
        self.save()
        # Pull is also a local mutation; pin the verified local Unix daemon.
        self.docker("pull", image, timeout=180)
        self.docker("volume", "create", "--label", LABEL + "=" + run_id, self.cfg["volume"])
        # Docker suppresses published ports on internal networks. Use a dedicated
        # isolated bridge (no existing services attached), only host-loopback DNAT.
        self.docker(
            "network",
            "create",
            "--opt",
            "com.docker.network.bridge.enable_icc=false",
            "--label",
            LABEL + "=" + run_id,
            self.cfg["network"],
        )
        variables = {
            "GITEA__database__DB_TYPE": "sqlite3",
            "GITEA__database__PATH": "/data/gitea/gitea.db",
            "GITEA__security__INSTALL_LOCK": "true",
            "GITEA__service__DISABLE_REGISTRATION": "true",
            "GITEA__service__REQUIRE_SIGNIN_VIEW": "true",
            "GITEA__server__ROOT_URL": self.origin + "/",
            "GITEA__server__DOMAIN": "127.0.0.1",
            "GITEA__server__DISABLE_SSH": "true",
            "GITEA__actions__ENABLED": "false",
            "GITEA__security__DISABLE_GIT_HOOKS": "true",
            "GITEA__repository__ENABLE_PUSH_CREATE_USER": "false",
            "GITEA__repository__ENABLE_PUSH_CREATE_ORG": "false",
            "GITEA__log__LEVEL": "Error",
            "GITEA__mailer__ENABLED": "false",
        }
        env = [part for key, value in variables.items() for part in ("--env", key + "=" + value)]
        self.docker(
            "run",
            "--detach",
            "--name",
            run_id,
            "--label",
            LABEL + "=" + run_id,
            "--pull",
            "never",
            "--network",
            self.cfg["network"],
            "--publish",
            f"127.0.0.1:{port}:3000",
            "--mount",
            "type=volume,src=" + self.cfg["volume"] + ",dst=/data",
            "--memory",
            "768m",
            "--cpus",
            "2",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges:true",
            *env,
            image,
        )
        self.runtime_boundary()
        self.readiness()
        self.docker("exec", self.cfg["container"], "chmod", "600", "/data/gitea/conf/app.ini")
        # CLI chooses the test-only password; capture it privately, never argv/logs.
        bootstrap = "bootstrap-" + nonce
        result = self.docker(
            "exec",
            "--user",
            "git",
            self.cfg["container"],
            "gitea",
            "--config",
            "/data/gitea/conf/app.ini",
            "admin",
            "user",
            "create",
            "--username",
            bootstrap,
            "--email",
            bootstrap + "@example.invalid",
            "--admin",
            "--random-password",
            "--must-change-password=false",
        )
        match = re.search(rb"generated random password is '([^\r\n]+)'", result.stdout, re.IGNORECASE)
        need(match, "bootstrap_password_not_found")
        self.cfg["bootstrap"] = {
            "username": bootstrap,
            "password": match.group(1).decode(),
        }
        self.save()
        for role in ROLES:
            username = role.replace("_", "-") + "-" + nonce
            password = secrets.token_urlsafe(32)
            status, raw, _ = self.http(
                "POST",
                API + "/admin/users",
                basic=(bootstrap, self.cfg["bootstrap"]["password"]),
                response_role="backend",
                body={
                    "username": username,
                    "email": username + "@example.invalid",
                    "password": password,
                    "must_change_password": False,
                    "send_notify": False,
                },
                expected=(201,),
            )
            user = json.loads(raw)
            need(user["is_admin"] is False, "test_actor_is_admin")
            _, token_raw, _ = self.http(
                "POST",
                API + "/users/" + username + "/tokens",
                basic=(username, password),
                response_role=role,
                body={
                    "name": run_id,
                    "scopes": [
                        "write:repository",
                        "write:issue",
                        "write:organization",
                        "read:user",
                    ],
                },
                expected=(201,),
            )
            token = json.loads(token_raw)
            self.cfg["accounts"][role] = {
                "username": username,
                "password": password,
                "id": user["id"],
                "token": token["sha1"],
            }
            self.save()
        self.api(
            "POST",
            "/orgs",
            body={"username": self.cfg["namespace"], "visibility": "private"},
            expected=(201,),
        )
        self.seed()
        self.cfg["bootstrapped"] = True
        self.save()

    def repo_path(self, key):
        return "/repos/" + self.cfg["namespace"] + "/" + self.cfg["repos"][key]["name"]

    def create_repo(self, key, *, name=None, pulls=True):
        name = name or key.replace("_", "-")
        _, repo, _ = self.api(
            "POST",
            "/orgs/" + self.cfg["namespace"] + "/repos",
            body={
                "name": name,
                "private": True,
                "auto_init": True,
                "default_branch": "main",
            },
            expected=(201,),
        )
        self.cfg["repos"][key] = {
            "name": name,
            "id": repo["id"],
            "marker": "PRODGUILD_T01_" + secrets.token_hex(16).upper(),
        }
        self.save()
        self.api(
            "PATCH",
            self.repo_path(key),
            body={
                "has_pull_requests": pulls,
                "has_actions": False,
                "has_wiki": False,
                "has_issues": pulls,
            },
        )
        return self.cfg["repos"][key]

    def file(self, key, path="probe.txt", *, branch="main", role="backend"):
        return self.api(
            "GET",
            self.repo_path(key) + "/contents/" + path + "?" + urlencode({"ref": branch}),
            role=role,
        )[1]

    def write(
        self,
        key,
        path,
        content,
        *,
        branch="main",
        role="backend",
        sha=None,
        create=False,
        expected=(201, 200),
    ):
        body = {
            "branch": branch,
            "content": base64.b64encode(content.encode()).decode(),
            "message": "prodguild T01 synthetic fixture",
        }
        if not create:
            body["sha"] = sha or self.file(key, path, branch=branch, role=role)["sha"]
        return self.api(
            "POST" if create else "PUT",
            self.repo_path(key) + "/contents/" + path,
            role=role,
            body=body,
            expected=expected,
        )

    def branch(self, key, name, *, old="main"):
        return self.api(
            "POST",
            self.repo_path(key) + "/branches",
            body={"new_branch_name": name, "old_branch_name": old},
            expected=(201,),
        )[1]

    def grant(self, key, role, permission="read"):
        self.api(
            "PUT",
            self.repo_path(key) + "/collaborators/" + self.cfg["accounts"][role]["username"],
            body={"permission": permission},
            expected=(204,),
        )

    def protect(self, key, name):
        self.api(
            "POST",
            self.repo_path(key) + "/branch_protections",
            body={
                "branch_name": name,
                "rule_name": name,
                "enable_push": False,
                "enable_force_push": False,
                "enable_merge_whitelist": True,
                "merge_whitelist_usernames": [],
                "required_approvals": 0,
                "merge_whitelist_teams": [],
                "block_admin_merge_override": True,
                "enable_bypass_allowlist": False,
                "bypass_allowlist_usernames": [],
                "bypass_allowlist_teams": [],
                "unprotected_file_patterns": "",
            },
            expected=(201,),
        )

    def seed(self):
        for key in (
            "starter",
            "reference",
            "draft_case",
            "pending",
            "solution_a",
            "solution_b",
            "scratch",
        ):
            self.create_repo(key, pulls=key in ("solution_a", "solution_b"))
            self.write(key, "probe.txt", self.cfg["repos"][key]["marker"] + "\n", create=True)
        for key in ("reference", "draft_case"):
            self.grant(key, "organizer")
        for role in ROLES[1:]:
            self.grant("starter", role)
            self.grant("scratch", role, "write")
            self.write("scratch", role + ".txt", "scratch\n", create=True)
        for role, key in (("student_a", "solution_a"), ("student_b", "solution_b")):
            for reader in (role, "reviewer", "expert"):
                self.grant(key, reader)
            self.write(key, "sample.txt", "context\nold\nremoved\n", create=True)
            self.write(key, "deleted.txt", "base-only\n", create=True)
            base = self.api("GET", self.repo_path(key) + "/branches/main")[1]["commit"]["id"]
            self.branch(key, "work")
            self.write(
                key,
                "probe.txt",
                self.cfg["repos"][key]["marker"] + "\nsubmitted\n",
                branch="work",
            )
            self.write(key, "sample.txt", "context\nnew\nadded\n", branch="work")
            old = self.file(key, "deleted.txt", branch="work")["sha"]
            self.api(
                "DELETE",
                self.repo_path(key) + "/contents/deleted.txt",
                body={"sha": old, "branch": "work", "message": "synthetic deletion"},
                expected=(200,),
            )
            head = self.api("GET", self.repo_path(key) + "/branches/work")[1]["commit"]["id"]
            identifier = secrets.token_hex(16)
            base_ref, head_ref = (
                "prodguild/bases/" + identifier,
                "prodguild/submissions/" + identifier,
            )
            self.branch(key, base_ref)
            self.branch(key, head_ref, old="work")
            self.branch(key, "retained-submission-" + key[-1], old="work")
            for ref in (base_ref, head_ref, "retained-submission-" + key[-1]):
                self.protect(key, ref)
            _, pull, _ = self.api(
                "POST",
                self.repo_path(key) + "/pulls",
                body={
                    "base": base_ref,
                    "head": head_ref,
                    "title": "Isolated submission " + key,
                    "body": self.cfg["repos"][key]["marker"],
                },
                expected=(201,),
            )
            _, review, _ = self.api(
                "POST",
                self.repo_path(key) + f"/pulls/{pull['number']}/reviews",
                body={
                    "event": "COMMENT",
                    "commit_id": head,
                    "body": self.cfg["repos"][key]["marker"] + " published backend representation",
                },
                expected=(200, 201),
            )
            _, comment, _ = self.api(
                "POST",
                self.repo_path(key) + f"/issues/{pull['number']}/comments",
                body={"body": self.cfg["repos"][key]["marker"] + " canonical projection source"},
                expected=(201,),
            )
            self.cfg["repos"][key].update(
                base=base,
                head=head,
                base_ref=base_ref,
                head_ref=head_ref,
                pull=pull["number"],
                review=review["id"],
                canonical_comment=comment["id"],
                canonical_body=comment["body"],
            )
            self.save()
            # Diverge work from C: reset to B, then create D (not descendant of C).
            git_dir = self.clone(key, "backend", suffix="seed")
            self.git(
                git_dir,
                "backend",
                ["push", "--force", self.clone_url(key), base + ":refs/heads/work"],
            )
            self.write(
                key,
                "probe.txt",
                self.cfg["repos"][key]["marker"] + "\nmoved work\n",
                branch="work",
            )
            self.cfg["repos"][key]["working"] = self.api("GET", self.repo_path(key) + "/branches/work")[1]["commit"][
                "id"
            ]
            self.save()
        for key in ("starter", "reference", "draft_case"):
            self.protect(key, "main")

    def clone_url(self, key):
        return self.origin + "/" + self.cfg["namespace"] + "/" + self.cfg["repos"][key]["name"] + ".git"

    def git(self, cwd, role, args, *, allowed=(0,), data=None):
        helper = self.directory / "askpass.local.py"
        if not helper.exists():
            private_write(
                helper,
                (
                    "#!" + sys.executable + "\nimport os,sys\np=sys.argv[1]\n"
                    "if p.startswith('Username'): print(os.environ['PRODGUILD_LAB_USERNAME'])\n"
                    "elif p.startswith('Password'): print(os.environ['PRODGUILD_LAB_TOKEN'])\n"
                    "else: sys.exit(1)\n"
                ).encode(),
            )
            helper.chmod(0o700)
        account = self.cfg["accounts"][role]
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.directory),
            "XDG_CONFIG_HOME": str(self.directory),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(helper),
            "GIT_ATTR_NOSYSTEM": "1",
            "PRODGUILD_LAB_USERNAME": account["username"],
            "PRODGUILD_LAB_TOKEN": account["token"],
        }
        command = [
            "/usr/bin/git",
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "init.templateDir=",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.sslVerify=true",
            *args,
        ]
        return process(command, cwd=cwd, env=env, data=data, timeout=30, allowed=allowed)

    def clone(self, key, role, *, suffix="", allowed=(0,)):
        destination = self.directory / ("git-" + key + "-" + role + "-" + suffix + secrets.token_hex(3))
        result = self.git(
            self.directory,
            role,
            [
                "clone",
                "--bare",
                "--no-local",
                "--no-hardlinks",
                self.clone_url(key),
                str(destination),
            ],
            allowed=allowed,
        )
        return destination if result.returncode == 0 else None

    def web(self, role):
        if role not in self.cookies:
            opener = build_opener(
                ProxyHandler({}),
                NoRedirect(),
                HTTPCookieProcessor(http.cookiejar.CookieJar()),
            )
            _, raw, _ = self.http("GET", "/user/login", cookie=opener, response_role=role)
            form = Form()
            form.feed(raw.decode())
            account = self.cfg["accounts"][role]
            fields = {"user_name": account["username"], "password": account["password"]}
            if form.csrf:
                fields["_csrf"] = form.csrf
            # v28 uses origin checks, not a hidden _csrf input. Keep exact same-origin
            # headers as a browser; never disable the server's CSRF protection.
            self.http(
                "POST",
                "/user/login",
                cookie=opener,
                response_role=role,
                body=urlencode(fields).encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": self.origin,
                    "Referer": self.origin + "/user/login",
                },
                expected=(302, 303),
            )
            self.cookies[role] = opener
        return self.cookies[role]

    def check(self, name, action):
        try:
            details = action() or {}
        except (LabError, OSError, ValueError, URLError) as error:
            # Only fixed harness codes; raw requests, exceptions, bodies stay private.
            code = str(error) if isinstance(error, LabError) else type(error).__name__
            self.rows.append({"id": name, "status": "failed", "code": code})
            print("FAILED " + name + " " + code, file=sys.stderr)
            return False
        self.rows.append({"id": name, "status": "passed", **details})
        return True

    def identity_control(self, role):
        account = self.cfg["accounts"][role]
        user = self.api("GET", "/user", role=role)[1]
        need(
            user["id"] == account["id"] and user["login"] == account["username"] and user["is_admin"] is False,
            "distinct_non_admin_identity_required",
        )
        own = {
            "backend": "pending",
            "organizer": "reference",
            "student_a": "solution_a",
            "student_b": "solution_b",
            "reviewer": "solution_a",
            "expert": "solution_a",
        }[role]
        repo = self.api("GET", self.repo_path(own), role=role)[1]
        need(
            repo["private"] is True and repo["id"] == self.cfg["repos"][own]["id"],
            "own_private_control_missing",
        )

    def forbidden_markers(self, role, *, key=None):
        # A shared starter must never carry either solution, even the actor's own.
        need(role is None or role in ROLES, "unknown_marker_actor")
        return tuple(
            self.cfg["repos"][repo]["marker"].encode("ascii")
            for repo, denied in ACL_DENIED.items()
            if (role is None or role in denied or (key == "starter" and repo in ("solution_a", "solution_b")))
            and "marker" in self.cfg.get("repos", {}).get(repo, {})
        )

    def inspect_body(self, raw, role, code, *, key=None, require_json=False):
        scan_markers(raw, self.forbidden_markers(role, key=key), code, require_json=require_json)

    def inspect_response(self, raw, role, path, *, headers):
        """Apply the response recipient's policy before parsing/status acceptance.

        Bootstrap has only the markers seeded so far. Backend controls are allowed
        private resources; their policy is never substituted for a denied actor's.
        """
        clean_path = unquote(path.split("?", 1)[0])
        key = next(
            (
                key
                for key, repo in self.cfg.get("repos", {}).items()
                if any(
                    clean_path == prefix or clean_path.startswith(prefix + "/")
                    for prefix in (
                        API + "/repos/" + self.cfg["namespace"] + "/" + repo["name"],
                        "/" + self.cfg["namespace"] + "/" + repo["name"],
                    )
                )
            ),
            None,
        )
        markers = self.forbidden_markers(role, key=key)
        if not markers or not raw:
            return
        encoding = next(
            (value for name, value in headers.items() if name.lower() == "content-encoding"),
            "identity",
        )
        need(encoding.lower() == "identity", "marker_scan_unsupported_content_encoding")
        scan_markers(
            raw,
            markers,
            "private_marker_in_response",
            require_json=clean_path.startswith(API + "/") and not clean_path.endswith(".diff"),
        )

    def api_denial(self, role, key, suffix, *, text=False):
        self.identity_control(role)
        target = self.repo_path(key) + suffix
        self.http("GET", API + target, role="backend")
        status, raw, _ = self.http("GET", API + target, role=role, expected=(403, 404))
        self.inspect_body(raw, role, "private_marker_in_denial", key=key, require_json=not text)
        if not text:
            envelope = json.loads(raw)
            need(
                isinstance(envelope, dict) and isinstance(envelope.get("message"), str),
                "denial_body_unrecognized",
            )
        self.http("GET", API + target, role="backend")
        self.identity_control(role)
        return status

    def web_denial(self, role, key, suffix=""):
        self.identity_control(role)
        cookie = self.web(role)
        own = "solution-b" if role == "student_b" else "reference" if role == "organizer" else "solution-a"
        self.http(
            "GET",
            "/" + self.cfg["namespace"] + "/" + own,
            cookie=cookie,
            response_role=role,
        )
        target = "/" + self.cfg["namespace"] + "/" + self.cfg["repos"][key]["name"] + suffix
        self.http("GET", target, cookie=self.web("backend"), response_role="backend")
        status, raw, _ = self.http("GET", target, cookie=cookie, response_role=role, expected=(403, 404))
        self.inspect_body(raw, role, "web_private_marker_visible", key=key)
        self.http("GET", target, cookie=self.web("backend"), response_role="backend")
        self.identity_control(role)
        return status

    def clone_denial(self, role, key):
        self.identity_control(role)
        own = "solution_b" if role == "student_b" else "reference" if role == "organizer" else "solution_a"
        self.clone(own, role, suffix="positive")
        self.clone(key, "backend", suffix="existence")
        destination = self.directory / ("denied-" + secrets.token_hex(4))
        result = self.git(
            self.directory,
            role,
            ["clone", "--bare", self.clone_url(key), str(destination)],
            allowed=(0, 1, 128),
        )
        classification = native_denial(result, self.clone_url(key), operation="clone", destination=destination)
        need(classification, "native_denial_not_authorization")
        self.clone(own, role, suffix="positive-after")
        self.identity_control(role)
        return {
            "git_exit": result.returncode,
            "denial_classification": classification,
            "positive_controls": "same-account-before-and-after",
        }

    def branch_sha(self, key, branch):
        return self.api("GET", self.repo_path(key) + "/branches/" + quote(branch, safe=""))[1]["commit"]["id"]

    def native_child(self, directory, role, parent):
        tree = self.git(directory, role, ["rev-parse", parent + "^{tree}"]).stdout.decode().strip()
        return (
            self.git(
                directory,
                role,
                [
                    "-c",
                    "user.name=Prodguild T01 Lab",
                    "-c",
                    "user.email=lab@example.invalid",
                    "commit-tree",
                    tree,
                    "-p",
                    parent,
                ],
                data=b"synthetic native permission probe\n",
            )
            .stdout.decode()
            .strip()
        )

    def permission_matrix(self):
        for key, roles in ACL_DENIED.items():
            repo = self.cfg["repos"][key]
            suffixes = ["", "/git/refs", "/contents/probe.txt?ref=main"]
            if "pull" in repo:
                n = repo["pull"]
                suffixes += [
                    f"/pulls/{n}",
                    f"/pulls/{n}/reviews",
                    f"/issues/{n}/comments",
                ]
            for role in roles:

                def api_action(role=role, key=key, suffixes=suffixes):
                    statuses = [self.api_denial(role, key, suffix) for suffix in suffixes]
                    # Diff is text, unlike the surrounding JSON API resources.
                    if "pull" in self.cfg["repos"][key]:
                        suffix = f"/pulls/{self.cfg['repos'][key]['pull']}.diff"
                        statuses.append(self.api_denial(role, key, suffix, text=True))
                    return {
                        "surfaces": len(statuses),
                        "http_statuses": sorted(set(statuses)),
                    }

                self.check(f"acl.api.{role}.{key}", api_action)
        for role in ("student_a", "student_b", "organizer", "reviewer", "expert"):
            targets = (
                ("reference", "draft_case", "pending", "solution_b")
                if role == "student_a"
                else ("reference", "draft_case", "pending", "solution_a")
                if role == "student_b"
                else ("pending", "solution_a", "solution_b")
                if role == "organizer"
                else ("reference", "draft_case", "pending")
            )
            for key in targets:

                def web_action(role=role, key=key):
                    suffixes = (
                        "",
                        "/src/branch/main/probe.txt",
                        "/raw/branch/main/probe.txt",
                        "/archive/main.zip",
                    )
                    if "pull" in self.cfg["repos"][key]:
                        suffixes += (f"/pulls/{self.cfg['repos'][key]['pull']}",)
                    return {
                        "surfaces": len(suffixes),
                        "http_statuses": sorted(set(self.web_denial(role, key, suffix) for suffix in suffixes)),
                    }

                self.check(f"acl.web.{role}.{key}", web_action)
                self.check(
                    f"acl.clone.{role}.{key}",
                    lambda role=role, key=key: self.clone_denial(role, key),
                )

    def useful_links(self):
        observations = []
        for role, key in (
            ("student_a", "solution_a"),
            ("student_b", "solution_b"),
            ("reviewer", "solution_a"),
            ("expert", "solution_a"),
            ("organizer", "reference"),
        ):
            self.identity_control(role)
            repo = self.api("GET", self.repo_path(key), role=role)[1]
            link = repo["html_url"]
            need(
                link.startswith(self.origin + "/") and not urlsplit(link).username,
                "unsafe_direct_link",
            )
            self.http(
                "GET",
                link[len(self.origin) :],
                cookie=self.web(role),
                response_role=role,
            )
            if "pull" in self.cfg["repos"][key]:
                pull = self.api(
                    "GET",
                    self.repo_path(key) + f"/pulls/{self.cfg['repos'][key]['pull']}",
                    role=role,
                )[1]
                self.http(
                    "GET",
                    pull["html_url"][len(self.origin) :],
                    cookie=self.web(role),
                    response_role=role,
                )
            self.clone(key, role, suffix="useful")
            observations.append(role + ":" + key)
        return {
            "authenticated_link_and_clone_controls": observations,
            "credential_free_urls": True,
        }

    def refs_protected(self):
        key, role = "solution_a", "student_a"
        repo = self.cfg["repos"][key]
        self.grant(key, role, "write")
        try:
            directory = self.clone(key, role, suffix="protected")
            child = self.native_child(directory, role, repo["head"])
            # Same actor/credential can really write a non-authoritative branch.
            self.git(
                directory,
                role,
                ["push", self.clone_url(key), child + ":refs/heads/native-positive"],
            )
            statuses = []
            classifications = []
            for branch, target in (
                (repo["head_ref"], repo["head"]),
                (repo["base_ref"], repo["base"]),
            ):
                need(
                    self.branch_sha(key, branch) == target,
                    "protected_precondition_changed",
                )
                for refspec, force in (
                    (child + ":refs/heads/" + branch, False),
                    (repo["working"] + ":refs/heads/" + branch, True),
                    (":refs/heads/" + branch, False),
                ):
                    args = ["push"] + (["--force"] if force else []) + [self.clone_url(key), refspec]
                    result = self.git(directory, role, args, allowed=(0, 1, 128))
                    classification = native_denial(
                        result,
                        self.clone_url(key),
                        operation="protected",
                        branch=branch,
                        source=refspec.split(":", 1)[0],
                        force=force,
                    )
                    need(classification, "protected_native_write_not_denied")
                    classifications.append(classification)
                blob = self.file(key, branch=branch)["sha"]
                status, body, _ = self.write(
                    key,
                    "probe.txt",
                    "forbidden update\n",
                    branch=branch,
                    role=role,
                    sha=blob,
                    expected=(403,),
                )
                need(
                    "cannot commit" in body.get("message", "").lower(),
                    "api_write_denial_not_authorization",
                )
                statuses.append(status)
                status, body, _ = self.api(
                    "DELETE",
                    self.repo_path(key) + "/branches/" + quote(branch, safe=""),
                    role=role,
                    expected=(403,),
                )
                need(
                    "branch protected" in body.get("message", "").lower(),
                    "api_delete_denial_not_protection",
                )
                statuses.append(status)
                need(self.branch_sha(key, branch) == target, "protected_ref_mutated")
            protections = self.api("GET", self.repo_path(key) + "/branch_protections")[1]
            for branch in (repo["head_ref"], repo["base_ref"]):
                rule = next(item for item in protections if item["rule_name"] == branch)
                need(
                    rule["enable_push"] is False
                    and rule["enable_force_push"] is False
                    and rule["block_admin_merge_override"] is True
                    and rule["enable_merge_whitelist"] is True,
                    "protection_configuration_not_applied",
                )
            self.api(
                "PATCH",
                self.repo_path(key) + "/branch_protections/" + quote(repo["head_ref"], safe=""),
                role=role,
                body={"enable_push": True},
                expected=(403,),
            )
            # Prove valid merge payload and same-account write privilege elsewhere.
            self.api(
                "PATCH",
                self.repo_path("scratch"),
                body={"has_pull_requests": True, "has_issues": True},
            )
            self.branch("scratch", "merge-positive")
            self.write(
                "scratch",
                "student_a.txt",
                "merge positive\n",
                branch="merge-positive",
                role=role,
            )
            _, positive, _ = self.api(
                "POST",
                self.repo_path("scratch") + "/pulls",
                role=role,
                body={
                    "base": "main",
                    "head": "merge-positive",
                    "title": "merge positive control",
                },
                expected=(201,),
            )
            self.api(
                "POST",
                self.repo_path("scratch") + f"/pulls/{positive['number']}/merge",
                role=role,
                body={"Do": "merge"},
                expected=(200, 201),
            )
            status, body, _ = self.api(
                "POST",
                self.repo_path(key) + f"/pulls/{repo['pull']}/merge",
                role=role,
                body={"Do": "merge"},
                expected=(405,),
            )
            need(
                body.get("message") == "User not allowed to merge PR",
                "merge_denial_not_authorization",
            )
            need(
                self.api("GET", self.repo_path(key) + f"/pulls/{repo['pull']}")[1]["merged"] is False,
                "submitted_pr_was_merged",
            )
            return {
                "native_denials": 6,
                "native_denial_classifications": classifications,
                "api_statuses": statuses,
                "merge_denial_status": status,
                "same_credential_positive_native_and_merge": True,
                "base": repo["base"],
                "head": repo["head"],
            }
        finally:
            self.grant(key, role, "read")

    def frozen_materials(self):
        classifications = []
        for key, role in (
            ("starter", "student_a"),
            ("reference", "organizer"),
            ("draft_case", "organizer"),
        ):
            self.identity_control(role)
            self.write("scratch", role + ".txt", "allowed frozen-resource control\n", role=role)
            scratch = self.clone("scratch", role, suffix="frozen-write-control")
            child = self.native_child(scratch, role, self.branch_sha("scratch", "main"))
            self.git(
                scratch,
                role,
                [
                    "push",
                    self.clone_url("scratch"),
                    child + ":refs/heads/control-" + secrets.token_hex(4),
                ],
            )
            before = self.branch_sha(key, "main")
            blob = self.file(key, role=role)["sha"]
            _, body, _ = self.write(
                key,
                "probe.txt",
                "forbidden frozen write\n",
                role=role,
                sha=blob,
                expected=(403,),
            )
            need(
                "write" in body.get("message", "").lower() or "cannot commit" in body.get("message", "").lower(),
                "frozen_api_denial_not_authorization",
            )
            directory = self.clone(key, role, suffix="frozen")
            child = self.native_child(directory, role, before)
            result = self.git(
                directory,
                role,
                ["push", self.clone_url(key), child + ":refs/heads/main"],
                allowed=(0, 1, 128),
            )
            classification = native_denial(
                result,
                self.clone_url(key),
                operation="write",
                branch="main",
                source=child,
            )
            need(classification, "frozen_native_denial_not_authorization")
            classifications.append(classification)
            need(self.branch_sha(key, "main") == before, "frozen_material_mutated")
        starter = self.api("GET", self.repo_path("starter"))[1]
        need(starter["has_pull_requests"] is False, "shared_starter_prs_enabled")
        return {
            "frozen_resources": 3,
            "shared_starter_prs_disabled": True,
            "native_denial_classifications": classifications,
        }

    def web_post(self, path, role, fields, expected=(200,)):
        return self.http(
            "POST",
            path,
            cookie=self.web(role),
            response_role=role,
            body=urlencode(fields).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.origin,
                "Referer": self.origin + path,
            },
            expected=expected,
        )

    def protected_web(self):
        role, key = "student_a", "solution_a"
        repo = self.cfg["repos"][key]
        prefix = "/" + self.cfg["namespace"]
        self.grant(key, role, "write")
        try:
            scratch = prefix + "/scratch"
            before = self.branch_sha("scratch", "main")
            self.web_post(
                scratch + "/_edit/main/student_a.txt",
                role,
                {
                    "tree_path": "student_a.txt",
                    "content": "web allowed write\n",
                    "commit_choice": "direct",
                    "commit_summary": "synthetic positive control",
                    "last_commit": before,
                },
            )
            need(
                self.branch_sha("scratch", "main") != before,
                "web_write_positive_failed",
            )
            self.branch("scratch", "web-delete-positive")
            self.web_post(scratch + "/branches/delete", role, {"name": "web-delete-positive"})
            self.api(
                "GET",
                self.repo_path("scratch") + "/branches/web-delete-positive",
                expected=(404,),
            )
            statuses = []
            for branch, sha in (
                (repo["base_ref"], repo["base"]),
                (repo["head_ref"], repo["head"]),
            ):
                status, body, _ = self.web_post(
                    prefix + "/" + repo["name"] + "/_edit/" + branch + "/probe.txt",
                    role,
                    {
                        "tree_path": "probe.txt",
                        "content": "forbidden\n",
                        "commit_choice": "direct",
                        "commit_summary": "synthetic denied attempt",
                        "last_commit": sha,
                    },
                    expected=(400, 403, 404),
                )
                need("protect" in body.decode().lower(), "web_edit_denial_not_protection")
                statuses.append(status)
                status, body, _ = self.web_post(
                    prefix + "/" + repo["name"] + "/branches/delete",
                    role,
                    {"name": branch},
                    expected=(200, 400),
                )
                need(
                    "protect" in body.decode().lower(),
                    "web_delete_denial_not_protection",
                )
                statuses.append(status)
                need(self.branch_sha(key, branch) == sha, "web_mutated_protected_ref")
            return {
                "cookie_authenticated_edit_and_delete_positive_controls": True,
                "protected_base_head_edit_delete_statuses": statuses,
            }
        finally:
            self.grant(key, role, "read")

    def discovery_privacy(self):
        for role, own, peer in (
            ("student_a", "solution_a", "solution_b"),
            ("student_b", "solution_b", "solution_a"),
        ):
            self.identity_control(role)
            forbidden = {self.cfg["repos"][key]["id"] for key in ("reference", "draft_case", "pending", peer)}
            for endpoint in ("/user/repos?limit=50", "/repos/search?limit=50&q="):
                _, raw, _ = self.http("GET", API + endpoint, role=role)
                self.inspect_body(raw, role, "private_marker_in_discovery", require_json=True)
                values = json.loads(raw)
                if endpoint.startswith("/repos/search"):
                    values = values["data"]
                ids = {r["id"] for r in values}
                need(
                    len(values) < 50 and self.cfg["repos"][own]["id"] in ids and not ids.intersection(forbidden),
                    "list_or_search_private_repository_disclosure",
                )
            _, page, _ = self.http("GET", "/explore/repos?q=", cookie=self.web(role), response_role=role)
            self.inspect_body(page, role, "private_marker_in_discovery")
            for key in ("reference", "draft_case", "pending", peer):
                path = "/" + self.cfg["namespace"] + "/" + self.cfg["repos"][key]["name"]
                need(
                    path.encode() not in page,
                    "web_discovery_discloses_private_repository",
                )
            # The same known commit is readable by its owner but not the other student.
            sha = self.cfg["repos"][peer]["head"]
            self.api_denial(role, peer, "/git/commits/" + sha)
        for key in ("reference", "draft_case", "solution_a", "solution_b", "pending"):
            path = self.repo_path(key)
            self.api("GET", path)
            _, raw, _ = self.http("GET", API + path, expected=(403, 404))
            self.inspect_body(raw, None, "anonymous_private_content", require_json=True)
            target = "/" + self.cfg["namespace"] + "/" + self.cfg["repos"][key]["name"]
            status, body, headers = self.http("GET", target, expected=(302, 303, 404))
            if status in (302, 303):
                need(
                    headers.get("Location", "").startswith("/user/login"),
                    "anonymous_redirect_not_login",
                )
            self.inspect_body(body, None, "anonymous_private_content")
        return {
            "student_api_list_search_web_explore": 6,
            "peer_known_commit_denials": 2,
            "anonymous_api_web_denials": 10,
            "anonymous_web_login_redirects_are_authentication_gate_only": True,
        }

    def blob_race(self):
        blob = self.file("scratch", "student_a.txt")["sha"]

        def contender(label):
            status, body, _ = self.write(
                "scratch",
                "student_a.txt",
                label + "\n",
                role="student_a",
                sha=blob,
                expected=(200, 403, 409, 422),
            )
            if status != 200:
                message = body.get("message", "").lower()
                need(
                    any(part in message for part in ("sha", "cannot lock ref", "does not match")),
                    "concurrent_error_not_stale_write",
                )
            return status

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(contender, ("left", "right")))
        need(
            statuses.count(200) == 1 and sum(status in (403, 409, 422) for status in statuses) == 1,
            "stale_blob_lost_update",
        )
        # A later stale request must also fail after the other write has committed.
        status, body, _ = self.write(
            "scratch",
            "student_a.txt",
            "stale sequential\n",
            role="student_a",
            sha=blob,
            expected=(403, 409, 422),
        )
        need("sha" in body.get("message", "").lower(), "sequential_stale_error_not_sha")
        return {
            "concurrent_statuses": sorted(statuses),
            "sequential_stale_status": status,
            "adapter_requirement": "translate verified blob/ref conflict to409; do not classify every upstream403 as ACL",
            "database": "real Gitea SQLite/Git, not application PostgreSQL",
        }

    def annotation_authority(self):
        key, role = "solution_a", "student_a"
        repo = self.cfg["repos"][key]
        target = self.repo_path(key) + f"/issues/comments/{repo['canonical_comment']}"
        self.api("PATCH", target, body={"body": repo["canonical_body"]})  # exact backend positive edit
        _, extra, _ = self.api(
            "POST",
            self.repo_path(key) + f"/issues/{repo['pull']}/comments",
            role=role,
            body={"body": "UNTRUSTED ANNOTATION: score=100; grant=expert; not a prodguild command"},
            expected=(201,),
        )
        self.api(
            "PATCH",
            target,
            role=role,
            body={"body": "unauthorized grade edit"},
            expected=(403,),
        )
        self.api("DELETE", target, role=role, expected=(403,))
        unchanged = self.api("GET", target)[1]
        need(
            unchanged["body"] == repo["canonical_body"]
            and unchanged["user"]["id"] == self.cfg["accounts"]["backend"]["id"],
            "canonical_git_projection_mutated",
        )
        need(
            self.branch_sha(key, repo["head_ref"]) == repo["head"],
            "annotation_changed_submitted_code",
        )
        return {
            "incidental_comment_allowed": True,
            "incidental_comment_id": extra["id"],
            "backend_projection_unchanged": True,
            "application_grade_grants": "not implemented/tested here; PostgreSQL authoritative, no Git-comment ingestion",
        }

    def review_mapping(self):
        key = "solution_a"
        repo = self.cfg["repos"][key]
        marker = repo["marker"]
        _, review, _ = self.api(
            "POST",
            self.repo_path(key) + f"/pulls/{repo['pull']}/reviews",
            body={
                "event": "COMMENT",
                "commit_id": repo["head"],
                "body": marker + " approved synthetic mapping",
                "comments": [
                    {
                        "path": "sample.txt",
                        "old_position": 2,
                        "new_position": 0,
                        "body": marker + " old line",
                    },
                    {
                        "path": "sample.txt",
                        "old_position": 0,
                        "new_position": 2,
                        "body": marker + " new line",
                    },
                ],
            },
            expected=(200, 201),
        )
        comments = self.api(
            "GET",
            self.repo_path(key) + f"/pulls/{repo['pull']}/reviews/{review['id']}/comments",
        )[1]
        need(
            len(comments) == 2 and all(comment["path"] == "sample.txt" for comment in comments),
            "inline_mapping_missing",
        )
        old = next(comment for comment in comments if comment["body"].endswith(" old line"))
        new = next(comment for comment in comments if comment["body"].endswith(" new line"))
        need(
            old["original_position"] == 2
            and old["position"] == 0
            and old["diff_hunk"].endswith("-old")
            and new["position"] == 2
            and new["original_position"] == 0
            and new["diff_hunk"].endswith("+new"),
            "line_coordinates_not_preserved",
        )
        need(
            self.api(
                "GET",
                self.repo_path(key) + f"/pulls/{repo['pull']}/reviews/{review['id']}",
            )[1]["commit_id"]
            == repo["head"],
            "review_not_pinned_to_submitted_head",
        )
        self.cfg["repos"][key]["mapped_review"] = review["id"]
        self.save()
        # Native file comments are not assumed; safe published general-comment fallback.
        _, fallback, _ = self.api(
            "POST",
            self.repo_path(key) + f"/issues/{repo['pull']}/comments",
            body={"body": "Published file feedback: path=sample.txt; retained_commit=" + repo["head"]},
            expected=(201,),
        )
        need(
            "sample.txt" in fallback["body"] and repo["head"] in fallback["body"],
            "fallback_file_metadata_missing",
        )
        # Pure path/side/diff validation is a future adapter responsibility, not delegated to Gitea.
        return {
            "review_id": review["id"],
            "inline_comments": len(comments),
            "old_new_file_line_coordinates": [2, 2],
            "published_file_comment_fallback": True,
            "native_file_comment_support_claimed": False,
        }

    def review_edges(self):
        repo = self.cfg["repos"]["solution_a"]
        observations = []
        for case, path, old, new in (
            ("out_of_range", "sample.txt", 0, 999),
            ("unchanged_file", "README.md", 0, 1),
            ("zero_coordinates", "sample.txt", 0, 0),
            ("deleted_old_line", "deleted.txt", 1, 0),
            ("context_new_line", "sample.txt", 0, 1),
        ):
            _, review, _ = self.api(
                "POST",
                self.repo_path("solution_a") + f"/pulls/{repo['pull']}/reviews",
                body={
                    "event": "COMMENT",
                    "commit_id": repo["head"],
                    "body": "synthetic compatibility edge",
                    "comments": [
                        {
                            "path": path,
                            "body": case,
                            "old_position": old,
                            "new_position": new,
                        }
                    ],
                },
                expected=(200,),
            )
            comments = self.api(
                "GET",
                self.repo_path("solution_a") + f"/pulls/{repo['pull']}/reviews/{review['id']}/comments",
            )[1]
            need(
                len(comments) == 1
                and comments[0]["path"] == path
                and comments[0]["position"] == new
                and comments[0]["original_position"] == old,
                "edge_coordinate_changed",
            )
            if case in ("out_of_range", "zero_coordinates"):
                need(comments[0]["diff_hunk"] == "", "unexpected_edge_hunk")
            elif case == "deleted_old_line":
                need(
                    comments[0]["diff_hunk"].endswith("-base-only"),
                    "deleted_line_mismatch",
                )
            elif case == "context_new_line":
                need(
                    comments[0]["diff_hunk"].endswith(" context"),
                    "context_line_mismatch",
                )
            observations.append(
                {
                    "case": case,
                    "native_status": 200,
                    "has_hunk": bool(comments[0]["diff_hunk"]),
                }
            )
        return {
            "observations": observations,
            "adapter_requirement": "reject invalid/non-diff coordinates before I/O; native acceptance is not validation",
            "file_level_policy": "zero coordinates accepted without hunk; use explicit published general-comment fallback",
        }

    def binary_roundtrip(self):
        payload = b"\x00\xffPRODGUILD_T01_BINARY\x80\x00"
        self.api(
            "POST",
            self.repo_path("scratch") + "/contents/synthetic.bin",
            role="student_a",
            body={
                "branch": "main",
                "content": base64.b64encode(payload).decode(),
                "message": "bounded synthetic binary",
            },
            expected=(201,),
        )
        metadata = self.file("scratch", "synthetic.bin", role="student_a")
        need(
            base64.b64decode(metadata["content"]) == payload and metadata["size"] == len(payload),
            "binary_base64_corrupted",
        )
        _, raw, _ = self.http(
            "GET",
            "/" + self.cfg["namespace"] + "/scratch/raw/branch/main/synthetic.bin",
            cookie=self.web("student_a"),
            response_role="student_a",
        )
        need(raw == payload, "binary_raw_corrupted")
        return {
            "bytes": len(payload),
            "sha256": digest(payload),
            "api_base64_and_web_raw_exact": True,
            "application_download_headers": "contract only; application route not implemented here",
        }

    def scan_private_git(self):
        blobs = 0
        classifications = []
        for key, role in (
            ("starter", "student_a"),
            ("starter", "student_b"),
            ("solution_a", "student_a"),
            ("solution_b", "student_b"),
        ):
            directory = self.clone(key, role, suffix="all-objects")
            self.git(directory, role, ["fetch", self.clone_url(key), "+refs/*:refs/*"])
            objects = self.git(directory, role, ["rev-list", "--objects", "--all"]).stdout.decode().splitlines()
            need(len(objects) < 500, "fixture_object_bound_exceeded")
            markers = self.forbidden_markers(role, key=key)
            for item in objects:
                sha = item.split()[0]
                kind = self.git(directory, role, ["cat-file", "-t", sha]).stdout.strip()
                if kind == b"blob":
                    body = self.git(directory, role, ["cat-file", "blob", sha]).stdout
                    need(len(body) < 65536, "fixture_blob_bound_exceeded")
                    scan_markers(body, markers, "private_content_in_reachable_git_history")
                    blobs += 1
            if key.startswith("solution_"):
                peer = "solution_b" if key == "solution_a" else "solution_a"
                result = self.git(
                    directory,
                    role,
                    ["fetch", self.clone_url(peer), self.cfg["repos"][peer]["head"]],
                    allowed=(0, 1, 128),
                )
                classification = native_denial(result, self.clone_url(peer), operation="fetch")
                need(classification, "peer_known_sha_denial_not_authorization")
                classifications.append(classification)
                self.git(directory, role, ["fetch", self.clone_url(key), "+refs/*:refs/*"])
        # Pending canary positively exists outside every student-readable repo.
        raw = base64.b64decode(self.file("pending")["content"])
        need(
            self.cfg["repos"]["pending"]["marker"].encode() in raw,
            "pending_positive_control_missing",
        )
        api_bodies = 0
        for key, role in (
            ("starter", "student_a"),
            ("starter", "student_b"),
            ("solution_a", "student_a"),
            ("solution_b", "student_b"),
        ):
            repo = self.cfg["repos"][key]
            suffixes = ["", "/contents/probe.txt?ref=main", "/git/refs"]
            if key.startswith("solution_"):
                suffixes += [
                    f"/pulls/{repo['pull']}",
                    f"/pulls/{repo['pull']}/reviews",
                    f"/issues/{repo['pull']}/comments",
                    f"/pulls/{repo['pull']}.diff",
                ]
            for suffix in suffixes:
                _, body, _ = self.http("GET", API + self.repo_path(key) + suffix, role=role)
                self.inspect_body(
                    body,
                    role,
                    "private_marker_in_student_api",
                    key=key,
                    require_json=not suffix.endswith(".diff"),
                )
                api_bodies += 1
        return {
            "reachable_blobs_scanned": blobs,
            "all_advertised_refs_fetched": True,
            "known_peer_sha_fetches_denied": 2,
            "native_denial_classifications": classifications,
            "student_api_bodies_scanned": api_bodies,
            "actor_forbidden_canaries_absent_in_inspected_bodies": True,
            "body_scan_encodings": "raw/UTF text, JSON keys/strings, diff/blob line payloads, four nested base64 layers",
            "pending_application_drafts": "PostgreSQL-only contract; no learner-repo projection performed",
        }

    def fork_hypothesis(self):
        self.create_repo("fork_source", pulls=True)
        self.write("fork_source", "probe.txt", "shared starter\n", create=True)
        for role in ("student_a", "student_b"):
            self.grant("fork_source", role)
        name = "private-fork-" + secrets.token_hex(4)
        _, fork, _ = self.api(
            "POST",
            self.repo_path("fork_source") + "/forks",
            role="student_a",
            body={"name": name},
            expected=(202,),
        )
        login = self.cfg["accounts"]["student_a"]["username"]
        path = "/repos/" + login + "/" + name
        end = time.monotonic() + 20
        while time.monotonic() < end:
            status, raw, _ = self.http("GET", API + path, role="student_a", expected=(200, 404))
            if status == 200:
                fork = json.loads(raw)
                break
            time.sleep(0.2)
        need(fork["private"] is True, "fork_did_not_inherit_private")
        self.api(
            "POST",
            path + "/branches",
            role="student_a",
            body={"new_branch_name": "solution", "old_branch_name": "main"},
            expected=(201,),
        )
        canary = "PRODGUILD_T01_SHARED_UPSTREAM_HYPOTHESIS_" + secrets.token_hex(8).upper()
        blob = self.api("GET", path + "/contents/probe.txt?ref=solution", role="student_a")[1]["sha"]
        self.api(
            "PUT",
            path + "/contents/probe.txt",
            role="student_a",
            body={
                "sha": blob,
                "branch": "solution",
                "content": base64.b64encode((canary + "\n").encode()).decode(),
                "message": "synthetic fork solution",
            },
        )
        direct_status = self.api("GET", path, role="student_b", expected=(403, 404))[0]
        _, pull, _ = self.api(
            "POST",
            self.repo_path("fork_source") + "/pulls",
            role="student_a",
            body={
                "base": "main",
                "head": login + ":solution",
                "title": "Rejected topology privacy hypothesis",
            },
            expected=(201,),
        )
        status, diff, _ = self.http(
            "GET",
            API + self.repo_path("fork_source") + f"/pulls/{pull['number']}.diff",
            role="student_b",
        )
        need(canary.encode() in diff, "shared_upstream_hypothesis_did_not_reproduce")
        return {
            "private_fork_inherited": True,
            "direct_peer_fork_status": direct_status,
            "shared_upstream_peer_pr_diff_status": status,
            "observed_peer_solution_disclosure": True,
            "decision": "reject shared-upstream PRs; use isolated per-project same-repository PRs",
        }

    def ambiguous_writes(self):
        outcomes = []
        for kind in ("repository", "pull_request", "review"):
            operation = "prodguild-t01-operation-" + secrets.token_hex(6)
            if kind == "repository":
                path = API + "/orgs/" + self.cfg["namespace"] + "/repos"
                name = "recover-" + secrets.token_hex(4)
                payload = {
                    "name": name,
                    "private": True,
                    "auto_init": True,
                    "description": operation,
                }
            else:
                repo = self.cfg["repos"]["solution_b"]
                if kind == "pull_request":
                    base, head = (
                        "recover-base-" + secrets.token_hex(4),
                        "recover-head-" + secrets.token_hex(4),
                    )
                    self.branch("solution_b", base)
                    self.branch("solution_b", head, old=repo["head_ref"])
                    path = API + self.repo_path("solution_b") + "/pulls"
                    payload = {
                        "base": base,
                        "head": head,
                        "title": operation,
                        "body": operation,
                    }
                else:
                    path = API + self.repo_path("solution_b") + f"/pulls/{repo['pull']}/reviews"
                    payload = {
                        "event": "COMMENT",
                        "commit_id": repo["head"],
                        "body": operation,
                    }
            # Intent is durable before I/O. This private lab journal demonstrates the
            # lookup algorithm, not the future PostgreSQL operation transaction.
            self.cfg.setdefault("recovery_intents", []).append({"kind": kind, "path": path, "payload": payload})
            self.save()
            status, external_id = self.lost_response(path, payload)
            need(status in (200, 201), "fault_proxy_upstream_write_not_successful")
            if kind == "repository":
                found = self.api("GET", "/repos/" + self.cfg["namespace"] + "/" + name)[1]
                need(
                    found["private"] is True
                    and found["description"] == operation
                    and found["owner"]["login"] == self.cfg["namespace"],
                    "recovered_repository_payload_mismatch",
                )
                matches = [found]
                lookup = "/repos/" + self.cfg["namespace"] + "/" + name
            elif kind == "pull_request":
                matches = [
                    p
                    for p in self.api(
                        "GET",
                        self.repo_path("solution_b") + "/pulls?state=all&limit=50",
                    )[1]
                    if p["head"]["ref"] == head and p["base"]["ref"] == base and p["body"] == operation
                ]
                lookup = self.repo_path("solution_b") + f"/pulls/{matches[0]['number']}" if matches else ""
            else:
                matches = [
                    r
                    for r in self.api(
                        "GET",
                        self.repo_path("solution_b") + f"/pulls/{repo['pull']}/reviews?limit=50",
                    )[1]
                    if r["body"] == operation
                ]
                lookup = self.repo_path("solution_b") + f"/pulls/{repo['pull']}/reviews/{external_id}"
            need(
                len(matches) == 1 and matches[0]["id"] == external_id,
                "ambiguous_write_lookup_not_unique",
            )
            if kind != "repository":
                need(
                    matches[0]["user"]["id"] == self.cfg["accounts"]["backend"]["id"],
                    "recovered_author_mismatch",
                )
            if kind == "review":
                need(
                    matches[0]["commit_id"] == repo["head"],
                    "recovered_review_commit_mismatch",
                )
            self.cfg["recovery_intents"][-1].update(lookup=lookup, external_id=external_id)
            self.save()
            outcomes.append(
                {
                    "kind": kind,
                    "upstream_status": status,
                    "recovered_id": external_id,
                    "matches": 1,
                    "blind_post_retry": False,
                }
            )
        # Actual same-key native calls create two annotations: no assumed request dedup.
        repo = self.cfg["repos"]["solution_b"]
        path = self.repo_path("solution_b") + f"/issues/{repo['pull']}/comments"
        header = {"Idempotency-Key": "prodguild-t01-native-key-" + secrets.token_hex(8)}
        ids = [
            self.api(
                "POST",
                path,
                body={"body": "native same-key experiment"},
                headers=header,
                expected=(201,),
            )[1]["id"]
            for _ in range(2)
        ]
        need(ids[0] != ids[1], "native_dedup_experiment_unexpected")
        self.cfg["recovery_outcomes"] = outcomes
        self.save()
        return {
            "actual_response_loss_reconciliations": outcomes,
            "same_native_request_key_created_distinct_comments": True,
        }

    def lost_response(self, path, payload):
        lab = self
        outcome = {}

        class DropAfterCommit(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                need(self.path == path, "fault_proxy_wrong_path")
                length = int(self.headers.get("Content-Length", "0"))
                need(0 < length < 65536, "fault_proxy_body_bound")
                request_body = self.rfile.read(length)
                status, raw, _ = lab.http(
                    "POST",
                    path,
                    role="backend",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                    expected=(200, 201),
                )
                outcome.update(status=status, id=json.loads(raw)["id"])
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()

        server = HTTPServer(("127.0.0.1", 0), DropAfterCommit)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        lost = False
        try:
            self.http(
                "POST",
                path,
                body=payload,
                origin=f"http://127.0.0.1:{server.server_port}",
            )
        except (http.client.RemoteDisconnected, URLError, ConnectionError):
            lost = True
        finally:
            thread.join(timeout=15)
            server.server_close()
        need(
            lost and not thread.is_alive() and outcome,
            "actual_lost_response_not_observed",
        )
        return outcome["status"], outcome["id"]

    def restart_maintenance(self):
        self.owned("container", self.cfg["container"])
        before = {}
        for key in ("solution_a", "solution_b"):
            repo = self.cfg["repos"][key]
            _, diff, _ = self.http(
                "GET",
                API + self.repo_path(key) + f"/pulls/{repo['pull']}.diff",
                role="backend",
            )
            before[key] = digest(diff)
            directory = self.clone(key, "backend", suffix="before-gc")
            result = self.git(
                directory,
                "backend",
                ["merge-base", "--is-ancestor", repo["head"], repo["working"]],
                allowed=(0, 1),
            )
            need(result.returncode == 1, "working_head_still_descends_from_submission")
            path = "/data/git/repositories/" + self.cfg["namespace"] + "/" + repo["name"] + ".git"
            for arguments in (
                ("reflog", "expire", "--expire=now", "--all"),
                ("gc", "--prune=now"),
            ):
                self.docker(
                    "exec",
                    "--user",
                    "git",
                    self.cfg["container"],
                    "git",
                    "-C",
                    path,
                    *arguments,
                )
        self.docker("restart", self.cfg["container"])
        self.readiness()
        for key in ("solution_a", "solution_b"):
            repo = self.cfg["repos"][key]
            need(
                self.branch_sha(key, repo["base_ref"]) == repo["base"]
                and self.branch_sha(key, repo["head_ref"]) == repo["head"],
                "retained_refs_changed_after_restart_gc",
            )
            directory = self.clone(
                key,
                "student_a" if key == "solution_a" else "student_b",
                suffix="after-gc",
            )
            for sha in (repo["base"], repo["head"]):
                self.git(directory, "backend", ["cat-file", "-e", sha + "^{commit}"])
            _, diff, _ = self.http(
                "GET",
                API + self.repo_path(key) + f"/pulls/{repo['pull']}.diff",
                role="backend",
            )
            need(digest(diff) == before[key], "pinned_diff_changed_after_restart")
        for intent in self.cfg.get("recovery_intents", []):
            need(
                self.api("GET", intent["lookup"])[1]["id"] == intent["external_id"],
                "recovered_object_lost_after_restart",
            )
        return {
            "project_count": 2,
            "expired_reflogs_and_git_gc_prune_now": True,
            "container_restart": True,
            "both_base_head_refs_retained": True,
            "pinned_diff_hashes": before,
            "recovered_objects_still_readable": len(self.cfg.get("recovery_intents", [])),
        }

    def runtime_boundary(self):
        value = self.owned("container", self.cfg["container"])
        self.owned("volume", self.cfg["volume"])
        network = self.owned("network", self.cfg["network"])
        need(
            set(network.get("Containers", {})) == {value["Id"]},
            "other_container_attached",
        )
        ports = value["NetworkSettings"]["Ports"]
        bindings = [(name, binding) for name, values in ports.items() for binding in (values or [])]
        need(
            len(bindings) == 1
            and bindings[0][0] == "3000/tcp"
            and bindings[0][1]["HostIp"] == "127.0.0.1"
            and bindings[0][1]["HostPort"] == str(urlsplit(self.origin).port),
            "unexpected_published_binding",
        )
        need(
            value["HostConfig"]["Privileged"] is False
            and len(value["Mounts"]) == 1
            and value["Mounts"][0]["Type"] == "volume"
            and value["Mounts"][0]["Name"] == self.cfg["volume"]
            and value["Mounts"][0]["Destination"] == "/data",
            "unsafe_container_permissions",
        )
        need(
            len(value["NetworkSettings"]["Networks"]) == 1
            and self.cfg["network"] in value["NetworkSettings"]["Networks"],
            "shared_network",
        )
        image = json.loads(self.docker("image", "inspect", self.cfg["image"]).stdout)[0]
        need(
            value["Image"] == image["Id"] and self.cfg["image"] in image["RepoDigests"],
            "runtime_image_digest_mismatch",
        )
        return image

    def run_matrix(self):
        def preflight():
            image = self.runtime_boundary()  # Before any credential-bearing request.
            self.readiness()
            version = self.api("GET", "/version")[1]["version"]
            need(version == self.cfg["release"], "runtime_release_mismatch")
            for role in ROLES:
                self.identity_control(role)
            return {
                "version": version,
                "image_id": image["Id"],
                "architecture": image["Architecture"],
                "separate_non_admin_actors": len(ROLES),
                "loopback_only": True,
                "sqlite_actual_database": True,
                "local_unix_daemon_pinned": True,
                "running_image_matches_pulled_digest": True,
            }

        if not self.check("release.runtime_and_isolation_preflight", preflight):
            return
        self.permission_matrix()
        self.check("links.authenticated_repo_pr_and_http_clone", self.useful_links)
        self.check("refs.native_api_merge_and_rule_protection", self.refs_protected)
        self.check("refs.authenticated_web_edit_and_delete", self.protected_web)
        self.check("privacy.discovery_known_sha_and_anonymous", self.discovery_privacy)
        self.check(
            "freeze.starter_reference_and_no_shared_solution_merge",
            self.frozen_materials,
        )
        self.check("files.actual_concurrent_stale_blob_race", self.blob_race)
        self.check(
            "authority.incidental_annotations_cannot_edit_backend_projection",
            self.annotation_authority,
        )
        self.check("reviews.pinned_old_new_positions_and_file_fallback", self.review_mapping)
        self.check(
            "reviews.native_invalid_positions_require_adapter_validation",
            self.review_edges,
        )
        self.check("files.binary_api_and_web_roundtrip", self.binary_roundtrip)
        self.check(
            "privacy.full_reachable_git_objects_and_pending_api_absence",
            self.scan_private_git,
        )
        self.check("topology.private_fork_shared_upstream_rejected", self.fork_hypothesis)
        self.check(
            "recovery.actual_response_loss_and_native_key_nondedup",
            self.ambiguous_writes,
        )
        self.check("durability.actual_git_gc_and_container_restart", self.restart_maintenance)
        self.cfg["last_rows"] = self.rows
        self.save()

    def write_evidence(self, destination):
        document = {
            "schema_version": 1,
            "scope": "authorized-disposable-local-gitea-only",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "release": self.cfg["release"],
            "image": self.cfg["image"],
            "image_tag": self.cfg["image_tag"],
            "database": "SQLite (ACL fixture only; production PostgreSQL compatibility NOT proven)",
            "http": "literal host loopback only; not production TLS proof",
            "ssh": "disabled/unpublished, not tested",
            "run_id": self.cfg["run_id"],
            "rows": self.rows,
            "native_denial_policy": {
                "version": NATIVE_DENIAL_POLICY,
                "matching": "closed C-locale diagnostic grammar, exact credential-free repository and protected ref",
                "surfaces": [
                    "forbidden clone",
                    "known peer SHA fetch",
                    "protected push/force/delete",
                    "frozen push",
                ],
                "classification_evidence": "fixed denial_classification/native_denial_classifications in each row",
            },
            "marker_scan_policy": {
                "version": MARKER_POLICY,
                "actor_forbidden_repositories": {
                    role: [key for key, roles in ACL_DENIED.items() if role in roles] for role in ROLES
                },
                "starter_additionally_excludes": ["solution_a", "solution_b"],
                "anonymous_excludes": list(ACL_DENIED),
                "body_encodings": [
                    "raw and UTF-8/16/32 text (BOM optional)",
                    "JSON keys/strings and Unicode escapes",
                    "line-leading JSON/base64 fragments including diff +/- payloads",
                    "nested canonical base64, optional trailing padding; declared encodings validated",
                ],
                "bounds": {
                    "body_bytes": BODY_LIMIT,
                    "aggregate_scanned_bytes": SCAN_BYTES,
                    "structure_depth": SCAN_DEPTH,
                    "structure_nodes": SCAN_NODES,
                    "nested_base64_layers": SCAN_BASE64_DEPTH,
                },
                "body_surfaces": [
                    "every HTTP response returned by this lab, with explicit token/cookie/basic/anonymous recipient",
                    "API raw bytes before JSON parsing, including write/delete/rule/merge/annotation denials",
                    "20 named student starter/own-solution API absence responses including PR diff",
                ],
                "git_blob_scope": "raw/structured text in enumerated reachable fixture blobs, each below 65536 bytes",
                "scope": "only inspected fixture responses/objects; not a universal encoding or surface scanner",
                "exhausted_bounds_or_ambiguous_encoding": "fail closed",
            },
            "local_matrix_passed": bool(self.rows) and all(row["status"] == "passed" for row in self.rows),
            "production_ready": False,
            "application_domain_runtime_tested": False,
            "downstream_released": False,
        }
        # Evidence has only explicitly constructed public fields, never raw upstream I/O.
        text = json.dumps(document, indent=2) + "\n"
        for account in self.cfg.get("accounts", {}).values():
            need(
                all(json.dumps(account[field])[1:-1] not in text for field in ("password", "token")),
                "evidence_contains_secret",
            )
        if self.cfg.get("bootstrap"):
            need(
                json.dumps(self.cfg["bootstrap"]["password"])[1:-1] not in text,
                "evidence_contains_bootstrap_secret",
            )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)

    def cleanup(self):
        need(
            self.cfg and self.cfg["run_id"].startswith("prodguild-t01-lab-"),
            "unknown_cleanup_run",
        )
        existing = []
        for kind, name in (
            ("container", self.cfg["container"]),
            ("volume", self.cfg["volume"]),
            ("network", self.cfg["network"]),
        ):
            # Enumerate exact names first: inspect errors alone cannot distinguish
            # absent resources from daemon/permission failure. Support interrupted
            # bootstrap/cleanup without skipping ownership checks on survivors.
            command = ("ps", "--all") if kind == "container" else (kind, "ls")
            names = (
                self.docker(
                    *command,
                    "--format",
                    "{{.Names}}" if kind == "container" else "{{.Name}}",
                )
                .stdout.decode()
                .splitlines()
            )
            if name not in names:
                continue
            self.owned(kind, name)
            existing.append((kind, name))
        for kind, name in existing:
            if kind == "container":
                self.docker("stop", "--time", "10", name)
            self.docker(kind, "rm", name)
        self.cfg["cleaned_up"] = True
        self.save()
        return {
            "run_id": self.cfg["run_id"],
            "owned_container_volume_network_removed": True,
            "unrelated_resources_modified": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "run", "cleanup"))
    parser.add_argument("--state", required=True)
    parser.add_argument("--release-lock")
    parser.add_argument("--evidence", default="tests/integration/gitea-spike/evidence/local-matrix.json")
    args = parser.parse_args()
    os.umask(0o077)
    lab = Lab(args.state)
    try:
        if args.command == "bootstrap":
            need(args.release_lock, "release_lock_required")
            lab.bootstrap(args.release_lock)
            print("Disposable local fixture bootstrapped; secrets only in ignored mode0600 fixture.")
        elif args.command == "cleanup":
            lab.cleanup()
            print("Removed only this run's labelled container/volume/network; preserved evidence and private fixture.")
        else:
            need(
                lab.cfg.get("bootstrapped") and not lab.cfg.get("cleaned_up"),
                "live_fixture_unavailable",
            )
            lab.run_matrix()
            lab.write_evidence(args.evidence)
            print("Sanitized local matrix evidence saved; no production or downstream approval.")
            return 0 if all(row["status"] == "passed" for row in lab.rows) else 1
    except (LabError, subprocess.TimeoutExpired) as error:
        print(
            "Lab stopped: " + (str(error) if isinstance(error, LabError) else "bounded_process_timeout"),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
