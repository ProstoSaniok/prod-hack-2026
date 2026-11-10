"""Real Caddy routing checks against synthetic upstreams, not business acceptance."""

from __future__ import annotations

import argparse
import shutil
import unittest

from fixture import ProxyFixture, request


class ProxyRouting(unittest.TestCase):
    fixture: ProxyFixture

    def test_backend_namespaces_are_not_stripped(self) -> None:
        for path in ("/api", "/api/", "/api/v1/echo?x=1&path=a%2Fb", "/auth", "/auth/"):
            with self.subTest(path=path):
                response = request(self.fixture.public_port, path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.json()["upstream"], "backend")
                self.assertEqual(response.json()["uri"], path)

    def test_json_posts_and_synthetic_credentials_survive(self) -> None:
        for path in ("/auth/sign_in/email", "/auth/sign_up/email", "/api/v1/echo"):
            with self.subTest(path=path):
                body = b'{"fixture":"not-real-credentials"}'
                response = request(
                    self.fixture.public_port,
                    path,
                    method="POST",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer fixture-only",
                        "Idempotency-Key": "fixture-key",
                    },
                )
                self.assertEqual(response.status, 200)
                data = response.json()
                self.assertEqual(data["method"], "POST")
                self.assertEqual(data["body"], body.decode())
                self.assertEqual(data["authorization"], "Bearer fixture-only")
                self.assertEqual(data["idempotency_key"], "fixture-key")
                self.assertEqual(data["host"], f"127.0.0.1:{self.fixture.public_port}")
                self.assertEqual(data["forwarded_proto"], "http")

    def test_auth_challenge_and_empty_sign_out(self) -> None:
        response = request(self.fixture.public_port, "/auth/me")
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(response.json(), {"detail": "Authentication failed"})
        response = request(self.fixture.public_port, "/auth/sign_out", method="POST")
        self.assertEqual(response.status, 204)
        self.assertEqual(response.body, b"")

    def test_backend_failures_and_location_are_preserved(self) -> None:
        for status in (403, 409, 422, 503):
            with self.subTest(status=status):
                response = request(self.fixture.public_port, f"/api/v1/status/{status}")
                self.assertEqual(response.status, status)
                self.assertEqual(response.json(), {"detail": "Synthetic failure"})
        response = request(self.fixture.public_port, "/api/v1/created", method="POST")
        self.assertEqual(response.status, 201)
        self.assertEqual(response.headers["location"], "/api/v1/created/fixture")

    def test_unknown_api_paths_are_json_not_spa(self) -> None:
        for path in ("/api/missing", "/api/v1/missing", "/auth/missing", "/api/healthcheck"):
            with self.subTest(path=path):
                response = request(self.fixture.public_port, path)
                self.assertEqual(response.status, 404)
                self.assertIn("application/json", response.headers["content-type"])
                self.assertEqual(response.json(), {"detail": "Not Found"})

    def test_documentation_allowlist_and_schema(self) -> None:
        for path in ("/docs", "/docs/oauth2-redirect", "/redoc"):
            with self.subTest(path=path):
                response = request(self.fixture.public_port, path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["x-fixture-upstream"], "backend")
                self.assertIn(b"/openapi.json", response.body)
        response = request(self.fixture.public_port, "/openapi.json")
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(response.json()["openapi"], "3.1.0")

    def test_operational_endpoints_are_not_forwarded_to_either_upstream(self) -> None:
        for path in (
            "/metrics",
            "/metrics?x=1",
            "/metrics/",
            "/metrics/child",
            "/%6detrics",
            "/api/../metrics",
            "/healthcheck",
            "/ready",
            "/ready?x=1",
        ):
            with self.subTest(path=path):
                response = request(self.fixture.public_port, path)
                self.assertEqual(response.status, 404)
                self.assertNotIn("x-fixture-upstream", response.headers)
                self.assertNotIn(b"Fixture SPA", response.body)

    def test_ui_and_non_allowlisted_paths_stay_ui(self) -> None:
        for path in (
            "/",
            "/ui-kit",
            "/sidebar",
            "/projects/deep-link",
            "/imgs/logo.svg",
            "/apiculture",
            "/authentication",
            "/docs-extra",
            "/docs/private",
            "/openapi.json.bak",
        ):
            with self.subTest(path=path):
                response = request(self.fixture.public_port, path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["x-fixture-upstream"], "frontend")
                self.assertIn("text/html", response.headers["content-type"])

    def test_z_backend_unavailable_is_not_spa(self) -> None:
        # Last test: only the fixture's own backend listener is stopped.
        backend = self.fixture.backend
        self.assertIsNotNone(backend)
        if backend is None:
            self.fail("Fixture backend was not started")
        backend.shutdown()
        backend.server_close()
        response = request(self.fixture.public_port, "/api/v1/echo")
        self.assertIn(response.status, (502, 503))
        self.assertNotIn(b"Fixture SPA", response.body)
        self.assertNotIn("x-fixture-upstream", response.headers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", default=shutil.which("caddy"), help="Already installed Caddy binary")
    args = parser.parse_args()
    binary = shutil.which(args.caddy) if args.caddy else None
    if binary is None:
        parser.error("Executable routing validation BLOCKED: no installed Caddy; no downloads/pulls allowed")
    with ProxyFixture(binary) as fixture:
        ProxyRouting.fixture = fixture
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ProxyRouting)
        )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
