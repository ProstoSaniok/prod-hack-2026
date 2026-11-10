import gzip
import logging
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import override

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from src.web_api.configuration import load_configuration
from src.web_api.entry_point import make_asgi_application
from src.web_api.ioc.make import make_ioc


@pytest.fixture
def otlp_receiver(monkeypatch: pytest.MonkeyPatch) -> Iterator[Queue[tuple[str, bytes]]]:
    requests: Queue[tuple[str, bytes]] = Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            requests.put((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("OTEL_SERVICE_NAME", "backend-test")
        monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
        monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")
        monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "2")
        # Leave batches pending to verify shutdown drains all three signals.
        monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "60000")
        monkeypatch.setenv("OTEL_BLRP_SCHEDULE_DELAY", "60000")
        monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "60000")
        try:
            yield requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("agent_mode", [False, True])
async def test_otlp_export_and_log_correlation(
    otlp_receiver: Queue[tuple[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_mode: bool,
) -> None:
    if agent_mode:
        monkeypatch.setenv("OTEL_LOGS_EXPORTER", "none")
        monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_COMPRESSION", "gzip")
    monkeypatch.setenv("OTEL_SERVICE_INSTANCE_ID", "backend-test-instance")
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.namespace=hack-led-03,deployment.environment=test,sage.group=test-group",
    )
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    access_logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access_logger, "propagate", False)
    monkeypatch.setattr(access_logger, "level", logging.INFO)
    access_handlers = list(access_logger.handlers)
    configuration = load_configuration(Path(__file__).parents[2] / "config.example.toml")
    trace_id = "1234567890abcdef1234567890abcdef"

    # App-local providers must work across repeated application lifecycles.
    for _ in range(2):
        app = make_asgi_application(make_ioc(configuration))

        @app.get("/telemetry-check/{item_id}")
        async def instrumented_endpoint(item_id: int) -> dict[str, int]:
            logging.getLogger("src.telemetry_test").info("request processed")
            access_logger.info("request access log")
            return {"id": item_id}

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            response = await client.get(
                "/telemetry-check/42",
                headers={"traceparent": f"00-{trace_id}-0123456789abcdef-01"},
            )
            assert response.status_code == 200
            assert (await client.get("/auth/me")).status_code == 401

        assert root.handlers == handlers
        assert root.level == level
        assert access_logger.handlers == access_handlers

        payloads: dict[str, list[bytes]] = {}
        while not otlp_receiver.empty():
            path, body = otlp_receiver.get_nowait()
            payloads.setdefault(path, []).append(body)
        assert set(payloads) == ({"/v1/traces"} if agent_mode else {"/v1/traces", "/v1/metrics", "/v1/logs"})

        traces = [
            resource
            for payload in payloads["/v1/traces"]
            for resource in ExportTraceServiceRequest.FromString(payload).resource_spans
        ]
        spans = [span for resource in traces for scope in resource.scope_spans for span in scope.spans]
        request_span = next(span for span in spans if span.name == "GET /telemetry-check/{item_id}")
        assert request_span.trace_id.hex() == trace_id
        assert request_span.parent_span_id.hex() == "0123456789abcdef"
        assert any(span.name == "GET /auth/me" for span in spans)
        assert all(
            any(
                attr.key == "service.name" and attr.value.string_value == "backend-test"
                for attr in r.resource.attributes
            )
            for r in traces
        )
        attributes = {attr.key: attr.value.string_value for attr in traces[0].resource.attributes}
        assert attributes["service.namespace"] == "hack-led-03"
        assert attributes["deployment.environment"] == "test"
        assert attributes["sage.group"] == "test-group"
        assert attributes["service.instance.id"] == "backend-test-instance"

        if agent_mode:
            continue

        logs = [
            record
            for payload in payloads["/v1/logs"]
            for resource in ExportLogsServiceRequest.FromString(payload).resource_logs
            for scope in resource.scope_logs
            for record in scope.log_records
        ]
        for message in ("request processed", "request access log"):
            matching = [record for record in logs if record.body.string_value == message]
            assert len(matching) == 1
            assert matching[0].trace_id == request_span.trace_id
            assert matching[0].span_id == request_span.span_id

        metrics = [
            metric
            for payload in payloads["/v1/metrics"]
            for resource in ExportMetricsServiceRequest.FromString(payload).resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        ]
        assert any(metric.name == "http.server.duration" and metric.histogram.data_points for metric in metrics)


async def test_telemetry_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    configuration = load_configuration(Path(__file__).parents[2] / "config.example.toml")
    app = make_asgi_application(make_ioc(configuration))
    assert app.state.telemetry is None
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        assert (await client.get("/healthcheck")).status_code == 200
