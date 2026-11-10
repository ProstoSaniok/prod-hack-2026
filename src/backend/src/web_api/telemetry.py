import logging
import os
import socket
from contextlib import ExitStack

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter  # noqa: PLC2701
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler  # noqa: PLC2701
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor  # noqa: PLC2701
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class Telemetry:
    def __init__(self, app: FastAPI) -> None:
        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "backend"),
            "service.instance.id": os.getenv("OTEL_SERVICE_INSTANCE_ID", socket.gethostname()),
        })
        self._traces = TracerProvider(resource=resource, shutdown_on_exit=False)
        self._traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self._metrics = MeterProvider(
            resource=resource,
            metric_readers=(
                [PeriodicExportingMetricReader(OTLPMetricExporter())]
                if os.getenv("OTEL_METRICS_EXPORTER", "otlp").lower() != "none"
                else []
            ),
            shutdown_on_exit=False,
        )
        self._logs = LoggerProvider(resource=resource, shutdown_on_exit=False)
        self._export_logs = os.getenv("OTEL_LOGS_EXPORTER", "otlp").lower() != "none"
        if self._export_logs:
            self._logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        self._handler = LoggingHandler(level=logging.INFO, logger_provider=self._logs)
        # Export failures stay on console rather than feeding the exporter again.
        self._handler.addFilter(lambda record: not record.name.startswith("opentelemetry."))
        self._loggers: list[logging.Logger] = []
        self._root_level = logging.getLogger().level
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self._traces,
            meter_provider=self._metrics,
            exclude_spans=["receive", "send"],
        )

    def start_logging(self) -> None:
        if not self._export_logs:
            return
        root = logging.getLogger()
        self._root_level = root.level
        root.setLevel(min(root.level, logging.INFO))
        self._loggers = [root]
        for name in ("uvicorn", "uvicorn.access"):
            logger = logging.getLogger(name)
            if not logger.propagate:
                self._loggers.append(logger)
        for logger in self._loggers:
            logger.addHandler(self._handler)

    def shutdown(self) -> None:
        for logger in self._loggers:
            logger.removeHandler(self._handler)
        logging.getLogger().setLevel(self._root_level)
        self._handler.close()
        with ExitStack() as stack:
            stack.callback(self._logs.shutdown)
            stack.callback(self._traces.shutdown)
            stack.callback(self._metrics.shutdown)


def configure_telemetry(app: FastAPI) -> Telemetry | None:
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return None
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    return Telemetry(app)
