import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from sage_iac.client import Client, NoRedirect, SageError, TENANT
from sage_iac.config import load
from sage_iac.reconcile import apply, plan
from sage_iac.resources import Dashboard, Environment, ScrapeCollection, System


class FakeClient:
    def __init__(self):
        self.rows = []
        self.writes = []
        self.authorized = True
        self.group_authorized = True

    def verify_tenant(self):
        if not self.authorized:
            raise SageError("Tenant denied")

    def verify_group(self, name):
        if not self.group_authorized:
            raise SageError("Group denied")

    def request(self, method, path, *, query=None, body=None):
        if method == "GET":
            return {
                "items": copy.deepcopy(self.rows),
                "pagination": {"total": len(self.rows)},
            }
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == "POST":
            self.rows.append({**body, "uuid": "server-generated-id"})
        elif method == "PATCH":
            self.rows[0].update(body)


def dashboard(**extra):
    return Dashboard(
        "dashboard.stage",
        {"tenant": TENANT, "name": "prodguild-stage-overview", **extra},
    )


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()

    def test_create_then_noop_and_no_duplicate(self):
        first = plan(self.client, [dashboard()])
        self.assertEqual(first[0].action, "create")
        self.assertFalse(self.client.writes)
        apply(self.client, first)
        second = plan(self.client, [dashboard()])
        self.assertEqual(second[0].action, "noop")
        apply(self.client, second)
        self.assertEqual(len(self.client.writes), 1)

    def test_update_requires_explicit_opt_in(self):
        self.client.rows = [{**dashboard().data, "uuid": "existing", "zoom": 1}]
        changes = plan(self.client, [dashboard(zoom=2)])
        with self.assertRaises(SageError):
            apply(self.client, changes)
        self.assertFalse(self.client.writes)
        apply(self.client, changes, allow_update=True)
        self.assertEqual(plan(self.client, [dashboard(zoom=2)])[0].action, "noop")

    def test_create_race_does_not_overwrite(self):
        changes = plan(self.client, [dashboard()])
        self.client.rows = [{**dashboard().data, "uuid": "other-writer"}]
        with self.assertRaises(SageError):
            apply(self.client, changes)
        self.assertFalse(self.client.writes)

    def test_revoked_tenant_prevents_mutations(self):
        changes = plan(self.client, [dashboard()])
        self.client.authorized = False
        with self.assertRaises(SageError):
            apply(self.client, changes)
        self.assertFalse(self.client.writes)

    def test_preflight_all_dependencies_before_any_creation(self):
        self.client.group_authorized = False
        with self.assertRaises(SageError):
            plan(
                self.client,
                [
                    dashboard(),
                    Environment(
                        "env", {"groupName": "group", "envName": "prodguild-stage"}
                    ),
                ],
            )
        self.assertFalse(self.client.writes)

    def test_unrelated_tenant_not_adopted(self):
        self.client.rows = [
            {"tenant": "other", "name": "prodguild-stage-overview", "uuid": "unrelated"}
        ]
        self.assertEqual(plan(self.client, [dashboard()])[0].action, "create")

    def test_ambiguous_remote_identity_fails_closed(self):
        self.client.rows = [
            {**dashboard().data, "uuid": "one"},
            {**dashboard().data, "uuid": "two"},
        ]
        with self.assertRaises(SageError):
            plan(self.client, [dashboard()])

    def test_unknown_server_defaults_do_not_cause_drift(self):
        self.client.rows = [
            {**dashboard().data, "uuid": "id", "zoom": 1, "createdAt": "server-date"}
        ]
        self.assertEqual(plan(self.client, [dashboard()])[0].action, "noop")

    def test_read_after_write_failure_is_not_success(self):
        changes = plan(self.client, [dashboard()])
        with patch.object(
            self.client,
            "request",
            return_value={"items": [], "pagination": {"total": 0}},
        ):
            with self.assertRaises(SageError):
                apply(self.client, changes)


class ConfigurationTests(unittest.TestCase):
    def test_committed_manifest(self):
        self.assertEqual(len(load("objects/manifest.json")), 1)

    def test_wrong_tenant(self):
        with self.assertRaises(SageError):
            Dashboard("bad", {"name": "prodguild-test", "tenant": "other"}).validate()

    def test_unknown_dashboard_properties_rejected(self):
        with self.assertRaises(SageError):
            dashboard(password="not-allowed").validate()

    def test_port_and_interval_validation(self):
        for fields in (
            {"port": 0},
            {"port": 8080, "scrapeInterval": 1000, "scrapeTimeout": 2000},
        ):
            with self.assertRaises(SageError):
                System(
                    "system",
                    {"groupName": "group", "systemName": "prodguild-backend", **fields},
                ).validate()

    def test_metrics_do_not_accept_credentials(self):
        with self.assertRaises(SageError):
            ScrapeCollection(
                "collection", {"authorization": {"credentials": "secret"}}
            ).validate()

    def test_inactive_metrics_definitions_validate(self):
        resources = load("examples/metrics/manifest.json")
        self.assertEqual(
            [type(r) for r in resources], [Environment, System, ScrapeCollection]
        )

    def test_non_finite_coordinates_rejected(self):
        with self.assertRaises(SageError):
            dashboard(zoom=float("nan")).validate()

    def test_missing_dependency_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "object.json").write_text(json.dumps(dashboard().data))
            for definition, dependencies in (
                ("object.json", ["missing"]),
                ("../outside.json", []),
            ):
                (root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "tenant": TENANT,
                            "resources": [
                                {
                                    "id": "test",
                                    "kind": "dashboard",
                                    "definition": definition,
                                    "depends_on": dependencies,
                                }
                            ],
                        }
                    )
                )
                with self.assertRaises(SageError):
                    load(root / "manifest.json")


class ClientTests(unittest.TestCase):
    def test_missing_token_rejected(self):
        with self.assertRaises(SageError):
            Client("")

    def test_redirects_rejected(self):
        with self.assertRaises(SageError):
            NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.example"
            )

    def test_mutations_not_retried_and_errors_redacted(self):
        client = Client("test-secret")
        with patch.object(
            client._opener,
            "open",
            side_effect=HTTPError("url", 503, "test-secret", {}, None),
        ) as request:
            with self.assertRaises(SageError) as caught:
                client.request("POST", "/resource", body={"sensitive": "test-secret"})
            self.assertEqual(request.call_count, 1)
            self.assertNotIn("test-secret", str(caught.exception))

    def test_other_tenant_group_rejected_even_if_server_ignores_filters(self):
        client = Client("test-token")
        with patch.object(
            client, "request", return_value=[{"name": "group", "tenantKey": "other"}]
        ):
            with self.assertRaises(SageError):
                client.verify_group("group")

    def test_server_session_not_unverified_jwt_controls_tenant(self):
        client = Client("test-token")
        with patch.object(
            client,
            "request",
            return_value={"tenantPermissions": {"other": ["sage.user"]}},
        ):
            with self.assertRaises(SageError):
                client.verify_tenant()


class MetricsContractTests(unittest.TestCase):
    def test_scoped_read_and_post_put_for_each_resource(self):
        for resource in load("examples/metrics/manifest.json"):
            client = FakeClient()
            remote = {**resource.data, "serverExtra": "ignored"}
            with patch.object(client, "request", return_value=[remote]) as request:
                self.assertEqual(resource.project(resource.read(client)), resource.data)
                self.assertEqual(
                    request.call_args.kwargs["query"],
                    {"groupName": resource.data["groupName"]},
                )
                if isinstance(resource, ScrapeCollection):
                    self.assertEqual(
                        request.call_args.args[1],
                        "/mcc/api/v2/ScraperTarget/collection",
                    )
            with patch.object(client, "request") as request:
                resource.create(client)
                self.assertEqual(request.call_args.args, ("POST", resource.endpoint))
                resource.update(client, remote)
                self.assertEqual(request.call_args.args, ("PUT", resource.endpoint))

    def test_other_group_object_is_not_matched(self):
        resource = Environment(
            "env", {"groupName": "my-group", "envName": "prodguild-stage"}
        )
        client = FakeClient()
        with patch.object(
            client,
            "request",
            return_value=[{"groupName": "other", "envName": "prodguild-stage"}],
        ):
            self.assertIsNone(resource.read(client))


if __name__ == "__main__":
    unittest.main()
