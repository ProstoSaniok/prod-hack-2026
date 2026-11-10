"""Disposable binary-only Caddy fixture; never starts application services."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self, cast

ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = ROOT / "infrastructure/configs/caddy/Caddyfile"


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, object]:
        return cast("dict[str, object]", json.loads(self.body))


def request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return Response(response.status, {key.lower(): value for key, value in response.getheaders()}, response.read())
    finally:
        connection.close()


class FixtureHandler(BaseHTTPRequestHandler):
    upstream: str

    def log_message(self, _format: str, *args: object) -> None:
        # Synthetic credentials/body must not reach stdout or access logs.
        pass

    def do_GET(self) -> None:
        self.respond()

    def do_POST(self) -> None:
        self.respond()

    def respond(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        path = self.path.split("?", 1)[0]
        headers = {"X-Fixture-Upstream": self.upstream}
        status = 200
        content_type = "application/json"
        data = json.dumps(
            {
                "upstream": self.upstream,
                "uri": self.path,
                "method": self.command,
                "body": body.decode(),
                "authorization": self.headers.get("Authorization"),
                "idempotency_key": self.headers.get("Idempotency-Key"),
                "host": self.headers.get("Host"),
                "forwarded_proto": self.headers.get("X-Forwarded-Proto"),
            },
        ).encode()
        if self.upstream == "frontend":
            content_type = "text/html"
            data = b"<!doctype html><title>Fixture SPA</title>"
        elif path in STATIC_RESPONSES:
            content_type, data = STATIC_RESPONSES[path]
        elif path == "/auth/me" and self.headers.get("Authorization") is None:
            status = 401
            headers["WWW-Authenticate"] = "Bearer"
            data = b'{"detail":"Authentication failed"}'
        elif path == "/auth/sign_out":
            status = 204
            data = b""
        elif path.startswith("/api/v1/status/"):
            status = int(path.rsplit("/", 1)[1])
            data = b'{"detail":"Synthetic failure"}'
        elif path == "/api/v1/created":
            status = 201
            headers["Location"] = "/api/v1/created/fixture"
        elif path not in {
            "/api",
            "/api/",
            "/api/v1/echo",
            "/auth",
            "/auth/",
            "/auth/me",
            "/auth/sign_up/email",
            "/auth/sign_in/email",
        }:
            status = 404
            data = b'{"detail":"Not Found"}'
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


def handler_for(upstream: str) -> type[BaseHTTPRequestHandler]:
    class Handler(FixtureHandler):
        pass

    Handler.upstream = upstream
    return Handler


STATIC_RESPONSES = {
    "/healthcheck": ("application/json", b'{"ok":true}'),
    "/openapi.json": ("application/json", b'{"openapi":"3.1.0","paths":{}}'),
    **dict.fromkeys(
        ("/docs", "/redoc", "/docs/oauth2-redirect"),
        ("text/html", b"<!doctype html><script>const schema = '/openapi.json';</script>"),
    ),
}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return cast("int", listener.getsockname()[1])


def render_config(source: str, *, public: int, metrics: int, backend: int, frontend: int) -> str:
    """Replace endpoints and add fixture isolation; preserve production handlers."""
    replacements = {
        "{\n": "{\n\tadmin off\n\tpersist_config off\n\tauto_https off\n",
        "\n:80 {": f"\nhttp://127.0.0.1:{public} {{\n\tbind 127.0.0.1",
        "\n:8404 {": f"\nhttp://127.0.0.1:{metrics} {{\n\tbind 127.0.0.1",
        "backend:8080": f"127.0.0.1:{backend}",
        "frontend:80": f"127.0.0.1:{frontend}",
    }
    if not source.startswith("{\n"):
        raise ValueError("Expected production global options first")
    for old, new in replacements.items():
        if old != "{\n" and source.count(old) != 1:
            raise ValueError(f"Expected one endpoint: {old!r}")
        source = source.replace(old, new, 1)
    return source


def object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected an object in adapted Caddy configuration")
    return cast("dict[str, object]", value)


def validate_loopback_listeners(adapted_json: str, *, public: int, metrics: int) -> None:
    """Fail closed unless every adapted HTTP listener is an expected loopback socket."""
    config = object_mapping(json.loads(adapted_json))
    apps = object_mapping(config.get("apps"))
    http = object_mapping(apps.get("http"))
    servers = object_mapping(http.get("servers"))
    if not servers:
        raise ValueError("No HTTP servers in adapted Caddy configuration")
    addresses: list[str] = []
    for server in servers.values():
        listeners = object_mapping(server).get("listen")
        if not isinstance(listeners, list) or not listeners:
            raise ValueError("Expected explicit nonempty HTTP listen addresses")
        for address in listeners:
            if not isinstance(address, str):
                raise ValueError("Expected an HTTP listen address string")
            addresses.append(address)
    expected = [f"127.0.0.1:{public}", f"127.0.0.1:{metrics}"]
    if public == metrics or sorted(addresses) != sorted(expected):
        raise ValueError("Adapted HTTP listeners must be exactly the two fixture loopback sockets")


class ProxyFixture:
    def __init__(self, binary: str) -> None:
        self.binary = str(Path(binary).resolve())
        self.stack = ExitStack()
        self.process: subprocess.Popen[bytes] | None = None
        self.public_port = 0
        self.backend: ThreadingHTTPServer | None = None

    def server(self, upstream: str) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(upstream))
        self.stack.callback(server.server_close)
        thread = threading.Thread(target=server.serve_forever, name=f"prodguild-t14-{upstream}", daemon=True)
        thread.start()
        self.stack.callback(thread.join, 5)
        self.stack.callback(server.shutdown)
        return server

    def __enter__(self) -> Self:
        try:
            directory = Path(
                self.stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="prodguild-t14-proxy-", dir=Path(__file__).parent)
                )
            )
            self.backend = self.server("backend")
            frontend = self.server("frontend")
            self.public_port = free_port()
            metrics_port = free_port()
            while metrics_port == self.public_port:
                metrics_port = free_port()
            config = directory / "Caddyfile"
            config.write_text(
                render_config(
                    CADDYFILE.read_text(),
                    public=self.public_port,
                    metrics=metrics_port,
                    backend=self.backend.server_port,
                    frontend=frontend.server_port,
                ),
            )
            environment = {
                "PATH": os.defpath,
                "HOME": str(directory),
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_DATA_HOME": str(directory / "data"),
            }
            subprocess.run([self.binary, "version"], env=environment, cwd=directory, check=True, timeout=15)
            adapted = subprocess.run(
                [self.binary, "adapt", "--config", str(config), "--adapter", "caddyfile"],
                env=environment,
                cwd=directory,
                check=True,
                timeout=15,
                capture_output=True,
                text=True,
            )
            validate_loopback_listeners(adapted.stdout, public=self.public_port, metrics=metrics_port)
            # Validate and launch the same inspected JSON, not a re-adapted Caddyfile.
            adapted_config = directory / "caddy.json"
            adapted_config.write_text(adapted.stdout)
            subprocess.run(
                [self.binary, "validate", "--config", str(adapted_config)],
                env=environment,
                cwd=directory,
                check=True,
                timeout=15,
            )
            log = self.stack.enter_context((directory / "caddy.log").open("wb"))
            self.process = subprocess.Popen(
                [self.binary, "run", "--config", str(adapted_config)],
                env=environment,
                cwd=directory,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            self.stack.callback(self.stop)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("Isolated Caddy exited before readiness")
                try:
                    if request(self.public_port, "/").status == 200:
                        return self
                except OSError:
                    pass
                time.sleep(0.05)
            raise RuntimeError("Isolated Caddy did not become ready within 10 seconds")
        except BaseException:
            self.stack.close()
            raise

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def __exit__(self, *_args: object) -> None:
        self.stack.close()
