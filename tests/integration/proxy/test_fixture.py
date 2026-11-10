"""Static guardrails and mock-fixture tests; NOT executable proxy validation."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from fixture import CADDYFILE, handler_for, render_config, request, validate_loopback_listeners
from test_proxy import main


class StaticPolicy(unittest.TestCase):
    def test_production_route_allowlist_is_explicit(self) -> None:
        source = CADDYFILE.read_text()
        self.assertIn(
            "@backend path /api /api/* /auth /auth/* /docs /docs/oauth2-redirect /openapi.json /redoc", source
        )
        self.assertIn("handle @backend {", source)
        self.assertNotIn("handle_path", source)
        self.assertNotIn("rewrite", source)
        self.assertIn("@private_backend path /metrics /metrics/* /healthcheck /ready", source)
        self.assertIn('respond "Not found" 404', source)
        self.assertIn("health_uri /healthcheck", source)
        self.assertEqual(source.count("max_size 5976883"), 2)

    def test_fixture_changes_endpoints_not_handlers(self) -> None:
        source = CADDYFILE.read_text()
        rendered = render_config(source, public=10001, metrics=10002, backend=10003, frontend=10004)
        self.assertIn("admin off", rendered)
        self.assertIn("persist_config off", rendered)
        self.assertIn("auto_https off", rendered)
        self.assertIn("http://127.0.0.1:10001 {", rendered)
        self.assertIn("http://127.0.0.1:10002 {", rendered)
        self.assertNotIn("backend:8080", rendered)
        self.assertNotIn("frontend:80", rendered)
        # Existing policy lines survive endpoint changes and added fixture-only isolation.
        for line in source.splitlines():
            if line.strip() and line not in {"{", ":80 {", ":8404 {"} and "reverse_proxy" not in line:
                self.assertIn(line, rendered)

    def test_both_fixture_sites_explicitly_bind_only_ipv4_loopback(self) -> None:
        rendered = render_config(CADDYFILE.read_text(), public=10001, metrics=10002, backend=10003, frontend=10004)
        for port in (10001, 10002):
            self.assertIn(f"\nhttp://127.0.0.1:{port} {{\n\tbind 127.0.0.1\n", rendered)
        self.assertEqual(rendered.count("\tbind 127.0.0.1\n"), 2)
        self.assertNotIn("bind 0.0.0.0", rendered)
        self.assertNotIn("bind ::", rendered)

    def test_fixture_only_adds_isolation_and_replaces_endpoints(self) -> None:
        source = CADDYFILE.read_text()
        restored = render_config(source, public=10001, metrics=10002, backend=10003, frontend=10004)
        for old, new in (
            ("{\n\tadmin off\n\tpersist_config off\n\tauto_https off\n", "{\n"),
            ("\nhttp://127.0.0.1:10001 {\n\tbind 127.0.0.1", "\n:80 {"),
            ("\nhttp://127.0.0.1:10002 {\n\tbind 127.0.0.1", "\n:8404 {"),
            ("127.0.0.1:10003", "backend:8080"),
            ("127.0.0.1:10004", "frontend:80"),
        ):
            restored = restored.replace(old, new, 1)
        self.assertEqual(restored, source)

    def test_fixture_fails_closed_if_production_endpoints_change(self) -> None:
        with self.assertRaises(ValueError):
            render_config("not a Caddyfile", public=1, metrics=2, backend=3, frontend=4)
        with self.assertRaises(ValueError):
            render_config(
                CADDYFILE.read_text().replace("backend:8080", "other:8080"), public=1, metrics=2, backend=3, frontend=4
            )


class AdaptedListenerGuard(unittest.TestCase):
    """Synthetic JSON exercises the guard only, never substitutes for Caddy adapt."""

    def test_expected_split_and_grouped_listeners_are_accepted(self) -> None:
        for servers in (
            {"public": {"listen": ["127.0.0.1:10001"]}, "metrics": {"listen": ["127.0.0.1:10002"]}},
            {"combined": {"listen": ["127.0.0.1:10002", "127.0.0.1:10001"]}},
        ):
            validate_loopback_listeners(
                json.dumps({"apps": {"http": {"servers": servers}}}), public=10001, metrics=10002
            )

    def test_wildcard_hostname_wrong_extra_and_duplicate_addresses_are_rejected(self) -> None:
        for address in (":10001", "0.0.0.0:10001", "[::]:10001", "[::1]:10001", "localhost:10001", "127.0.0.1:9999"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_loopback_listeners(
                    json.dumps({"apps": {"http": {"servers": {"s": {"listen": [address, "127.0.0.1:10002"]}}}}}),
                    public=10001,
                    metrics=10002,
                )
        for addresses in (
            ["127.0.0.1:10001"],
            ["127.0.0.1:10001", "127.0.0.1:10002", "127.0.0.1:10003"],
            ["127.0.0.1:10001", "127.0.0.1:10002", "127.0.0.1:10001"],
        ):
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                validate_loopback_listeners(
                    json.dumps({"apps": {"http": {"servers": {"s": {"listen": addresses}}}}}),
                    public=10001,
                    metrics=10002,
                )

    def test_missing_malformed_or_implicit_listeners_are_rejected(self) -> None:
        invalid_servers: tuple[object, ...] = (
            None,
            [],
            {},
            {"s": None},
            {"s": {}},
            {"s": {"listen": []}},
            {"s": {"listen": [None]}},
            {"s": {"listen": "127.0.0.1:10001"}},
        )
        for servers in invalid_servers:
            with self.subTest(servers=servers), self.assertRaises(ValueError):
                validate_loopback_listeners(
                    json.dumps({"apps": {"http": {"servers": servers}}}), public=10001, metrics=10002
                )
        for config in ("not JSON", "null", "[]", "{}", '{"apps":{}}', '{"apps":{"http":{}}}'):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_loopback_listeners(config, public=10001, metrics=10002)


class MockFixture(unittest.TestCase):
    def test_binary_from_path_is_resolved_before_fixture_creation(self) -> None:
        with (
            patch("sys.argv", ["test_proxy.py", "--caddy", "caddy"]),
            patch("test_proxy.shutil.which", return_value="/installed/caddy"),
            patch("test_proxy.ProxyFixture", side_effect=RuntimeError("fixture not launched")) as fixture,
            self.assertRaisesRegex(RuntimeError, "fixture not launched"),
        ):
            main()
        fixture.assert_called_once_with("/installed/caddy")

    def test_synthetic_upstream_echo_and_auth_envelope(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for("backend"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = request(
                server.server_port,
                "/api/v1/echo?x=1",
                method="POST",
                body=b'{"fixture":true}',
                headers={"Authorization": "Bearer fixture-only"},
            )
            self.assertEqual(response.json()["uri"], "/api/v1/echo?x=1")
            self.assertEqual(response.json()["body"], '{"fixture":true}')
            self.assertEqual(response.json()["authorization"], "Bearer fixture-only")
            response = request(server.server_port, "/auth/me")
            self.assertEqual(response.status, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
            self.assertEqual(response.json(), {"detail": "Authentication failed"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
