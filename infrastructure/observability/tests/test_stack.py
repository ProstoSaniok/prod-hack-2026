"""Opt-in Docker smoke test: real exporters/storage/collector, local Sage mocks."""

import copy
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from check_sage import ROOT


def compose_model():
    return json.loads(
        subprocess.check_output(
            ["docker", "compose", "--profile", "sage", "config", "--format", "json"],
            cwd=ROOT,
            text=True,
        )
    )


class ComposeContractTests(unittest.TestCase):
    def test_private_listeners_and_opt_in_forwarding(self):
        model = compose_model()
        for name in ("postgres-exporter", "redis-exporter", "otel-collector"):
            self.assertFalse(model["services"][name].get("ports"))
        for name in ("victoriametrics", "vmagent"):
            for port in model["services"][name]["ports"]:
                self.assertEqual(port["host_ip"], "127.0.0.1")
        self.assertEqual(model["services"]["otel-collector"]["profiles"], ["sage"])
        self.assertNotIn("otel-collector", model["services"]["backend"]["depends_on"])


@unittest.skipUnless(
    os.getenv("PRODGUILD_OBSERVABILITY_INTEGRATION") == "1",
    "set PRODGUILD_OBSERVABILITY_INTEGRATION=1 to run isolated Docker smoke test",
)
class StackSmokeTests(unittest.TestCase):
    def run_compose(self, *args, check=True):
        result = subprocess.run(
            ["docker", "compose", "-p", self.project, "-f", str(self.filename), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def setUp(self):
        self.project = "prodguild-observability-test-" + uuid.uuid4().hex[:10]
        self.temp = tempfile.TemporaryDirectory(prefix=self.project + "-")
        self.addCleanup(self.temp.cleanup)
        self.filename = Path(self.temp.name) / "compose.json"
        model = compose_model()
        names = (
            "postgresql",
            "valkey",
            "postgres-exporter",
            "redis-exporter",
            "vmagent",
            "victoriametrics",
            "otel-collector",
        )
        model["services"] = {name: model["services"][name] for name in names}
        model.pop("name", None)
        model["networks"] = {"default": {"driver": "bridge"}}
        model["volumes"] = {
            name: {} for name in ("postgresql_data", "valkey_data", "vmagent_data", "victoriametrics_data")
        }
        for service in model["services"].values():
            service.pop("profiles", None)
            service["restart"] = "no"
            service.pop("ports", None)
        model["services"]["postgresql"]["environment"] = {
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
            "POSTGRES_DB": "postgres",
        }
        # PostgreSQL 18 rejects the pre-existing deployment's legacy mount. Only
        # this fresh test volume gets the new layout; never migrate user data here.
        model["services"]["postgresql"]["volumes"][0]["target"] = "/var/lib/postgresql"
        # Use image defaults for this isolated database: the deployment HBA also
        # lacks the local connection rule needed by the image's first-run init.
        model["services"]["postgresql"].pop("configs")
        model["services"]["postgresql"].pop("command")
        model["services"]["postgres-exporter"]["environment"] = {
            "DATA_SOURCE_URI": "postgresql:5432/postgres?sslmode=disable",
            "DATA_SOURCE_USER": "postgres",
            "DATA_SOURCE_PASS": "postgres",
        }
        model["services"]["redis-exporter"]["environment"] = {
            "REDIS_ADDR": "redis://valkey:6379",
            "REDIS_PASSWORD": "valkey",
        }
        model["services"]["otel-collector"]["environment"] = {
            "SAGE_REMOTE_WRITE_ENDPOINT": "http://sage-gateway:8080/api/v1/write",
            "SAGE_REMOTE_WRITE_AUTHORIZATION": "JWT test-only",
            "SAGE_GROUP": "test-group",
            "SAGE_SYSTEM": "prodguild",
            "SAGE_ENV": "test",
            "SAGE_DC": "test-dc",
            "SAGE_AGENT_OTLP_ENDPOINT": "http://sage-gateway:8080",
        }
        model["services"]["sage-metrics"] = copy.deepcopy(model["services"]["victoriametrics"])
        # The mock destination must never share the source's data volume.
        model["services"]["sage-metrics"].pop("volumes")
        model["services"]["sage-gateway"] = {
            "image": os.getenv("OBSERVABILITY_TEST_PYTHON_IMAGE", "docker.io/python:3.12-alpine"),
            "command": ["python", "/test/sink.py"],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(Path(__file__).with_name("sink.py")),
                    "target": "/test/sink.py",
                    "read_only": True,
                }
            ],
        }
        model["services"]["backend"] = copy.deepcopy(model["services"]["sage-gateway"])
        for name, target in (
            ("victoriametrics", 8428),
            ("sage-metrics", 8428),
            ("vmagent", 8429),
            ("otel-collector", 4318),
            ("sage-gateway", 8080),
        ):
            model["services"][name]["ports"] = [{"target": target, "published": "0", "host_ip": "127.0.0.1"}]
        self.filename.write_text(json.dumps(model))
        # Only this test-created UUID project and its volumes may be removed.
        self.addCleanup(self.run_compose, "down", "--volumes", "--remove-orphans", check=False)
        startup = self.run_compose("up", "-d", "--wait", "--wait-timeout", "90", check=False)
        if startup.returncode:
            logs = self.run_compose("logs", "--tail", "40", check=False)
            self.fail(startup.stderr + logs.stdout + logs.stderr)

    def endpoint(self, service, port):
        address = self.run_compose("port", service, str(port)).stdout.strip()
        return "http://" + address

    def get(self, url):
        with urlopen(url, timeout=10) as response:
            return json.load(response)

    def post(self, url, body):
        request = Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as response:
            return response.status

    def wait_for(self, condition):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if condition():
                    return
            except (URLError, KeyError):
                pass
            time.sleep(2)
        logs = self.run_compose("logs", "--tail", "50", check=False)
        self.fail("Smoke test timed out:\n" + logs.stdout + logs.stderr)

    def test_metrics_and_traces_reach_mock_sage(self):
        source = self.endpoint("victoriametrics", 8428)
        destination = self.endpoint("sage-metrics", 8428)
        gateway = self.endpoint("sage-gateway", 8080)
        collector = self.endpoint("otel-collector", 4318)

        def query(base, expression):
            url = base + "/api/v1/query?" + urlencode({"query": expression})
            return self.get(url)["data"]["result"]

        # Real PostgreSQL 18 and Valkey 9 must be reachable, not merely exporters' HTTP ports.
        for metric in ("pg_up", "redis_up", "prodguild_smoke", "prodguild_calls_total"):
            self.wait_for(lambda metric=metric: any(item["value"][1] == "1" for item in query(source, metric)))
            self.wait_for(
                lambda metric=metric: any(
                    item["value"][1] == "1"
                    and all(
                        item["metric"].get(key) == value
                        for key, value in {
                            "group": "test-group",
                            "system": "prodguild",
                            "env": "test",
                            "dc": "test-dc",
                        }.items()
                    )
                    for item in query(destination, metric)
                )
            )
        self.assertEqual(query(destination, "pg_up")[0]["metric"]["job"], "postgresql")
        self.assertEqual(query(destination, "redis_up")[0]["metric"]["job"], "valkey")

        now = time.time_ns()
        resource = {"attributes": [{"key": "service.name", "value": {"stringValue": "backend"}}]}
        span = {
            "traceId": "0123456789abcdef0123456789abcdef",
            "spanId": "0123456789abcdef",
            "name": "prodguild-smoke",
            "startTimeUnixNano": str(now),
            "endTimeUnixNano": str(now + 1000000),
        }
        payload = {"resourceSpans": [{"resource": resource, "scopeSpans": [{"spans": [span]}]}]}
        self.assertEqual(self.post(collector + "/v1/traces", payload), 200)
        self.wait_for(lambda: bool(self.get(gateway + "/stats")["trace_ids"]))
        self.assertEqual(self.get(gateway + "/stats")["rejected"], 0)

        # An unavailable destination must be retried without stopping local collection.
        before = self.get(gateway + "/stats")["metrics"]
        self.post(gateway + "/fail-next", {})
        self.wait_for(lambda: self.get(gateway + "/stats")["metrics"] > before)
        self.assertGreater(self.get(gateway + "/stats")["transient_failures"], 0)
        self.assertTrue(query(source, "pg_up"))
