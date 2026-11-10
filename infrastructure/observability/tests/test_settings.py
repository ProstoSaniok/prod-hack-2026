import unittest

from check_sage import validate


class SageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "SAGE_REMOTE_WRITE_ENDPOINT": "https://example.invalid/frost_remote_write/api/v1/write",
            "SAGE_REMOTE_WRITE_AUTHORIZATION": "JWT test-only",
            "SAGE_GROUP": "test-group",
            "SAGE_SYSTEM": "prodguild",
            "SAGE_ENV": "test",
            "SAGE_DC": "test-dc",
            "SAGE_AGENT_OTLP_ENDPOINT": "http://host.docker.internal:4318",
        }

    def test_complete_settings(self):
        self.assertEqual(validate(self.settings), [])

    def test_missing_settings(self):
        self.assertEqual(len(validate({})), 7)

    def test_all_routing_labels_are_required(self):
        for key in ("SAGE_GROUP", "SAGE_SYSTEM", "SAGE_ENV", "SAGE_DC"):
            with self.subTest(key=key):
                self.assertTrue(validate(self.settings | {key: ""}))

    def test_remote_write_requires_tls_and_no_url_credentials(self):
        for url in (
            "https://[",
            "http://example.invalid/api/v1/write",
            "https://user:secret@example.invalid/api/v1/write",
            "https://example.invalid/api/v1/write?token=secret",
        ):
            with self.subTest(url=url):
                self.assertTrue(validate(self.settings | {"SAGE_REMOTE_WRITE_ENDPOINT": url}))

    def test_ingestion_auth_is_not_management_bearer_auth(self):
        for auth in ("", "Bearer test-only", "JWT ", "JWT <token>", "JWT test\nheader"):
            with self.subTest(auth=auth):
                self.assertTrue(validate(self.settings | {"SAGE_REMOTE_WRITE_AUTHORIZATION": auth}))
