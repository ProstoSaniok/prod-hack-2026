# Local metrics and Sage forwarding

Root Compose includes PostgreSQL/Valkey exporters, vmagent and single-node
VictoriaMetrics. Profile `sage` adds an OpenTelemetry Collector. Application
release ordering is documented in [`deploy/README.md`](../../deploy/README.md).

## Signals and local storage

```text
postgres-exporter:9187 + redis-exporter:9121 + backend:8080/metrics
                         -> vmagent:8429 -> VictoriaMetrics:8428
VictoriaMetrics /federate -> otel-collector -> Sage HTTPS Remote Write
backend OTLP traces      -> otel-collector -> host Sage Agent:4318
container stdout/stderr                    -> host Sage Agent logs
```

Scraping runs every 30 seconds. VictoriaMetrics retains seven days by default
(`VICTORIAMETRICS_RETENTION`); vmagent has a persistent 1 GB remote-write queue.
Volumes are `victoriametrics_data` and `vmagent_data`. Exporters and Collector
have no host ports. Diagnostics bind loopback: VictoriaMetrics 18428 and vmagent
18429 (`VICTORIAMETRICS_PORT` / `VMAGENT_PORT`). Do not expose telemetry in Caddy.

Images and override variables are declared in
[`deploy/compose/compose.observability.yaml`](../../deploy/compose/compose.observability.yaml).
Use approved mirrors when necessary; old image-discovery records do not prove
current registry availability.

Set exporter credentials in ignored `infrastructure/configs/postgres-exporter/.env`
and `infrastructure/configs/redis-exporter/.env`. PostgreSQL uses separate
`DATA_SOURCE_URI`, `DATA_SOURCE_USER`, `DATA_SOURCE_PASS`; a dedicated `pg_monitor`
login is appropriate for deployment. Redis exporter credentials must match actual
Valkey ACL/requirepass configuration, not merely an unused environment variable.

From the repository root, once the intended data services are running:

```sh
docker compose config --quiet
docker compose up -d postgres-exporter redis-exporter victoriametrics vmagent
curl -fsS 'http://127.0.0.1:18428/api/v1/query?query=pg_up'
curl -fsS 'http://127.0.0.1:18428/api/v1/query?query=redis_up'
curl -fsS http://127.0.0.1:18429/targets
```

Exporter HTTP health does not prove database access: inspect `pg_up=1` and
`redis_up=1`. An unavailable backend has `up{job="backend"}=0`; start it through
its migration dependency. Existing root PostgreSQL storage/first-run caveats are
in the deployment guide; never erase application volumes to run monitoring.

## Sage forwarding

1. Obtain the authorized ingestion group/DC and HTTPS Frost Remote Write endpoint
   (documented path `/frost_remote_write/api/v1/write`). Do not substitute the Sage
   web UI URL. Use a Sage ingestion credential, never a GitLab token.
2. Copy `infrastructure/configs/otel-collector/.env.template` to ignored `.env`
   with mode 0600. Set `SAGE_REMOTE_WRITE_ENDPOINT`,
   `SAGE_REMOTE_WRITE_AUTHORIZATION='JWT <ingestion-token>'`, `SAGE_GROUP`,
   `SAGE_SYSTEM=prodguild`, `SAGE_ENV` and `SAGE_DC`. Mandatory labels are
   `group/system/env/dc`. Management-API Bearer auth is a separate contract.
3. Configure the host Sage Agent's Custom App source named exactly `backend`,
   matching `OTEL_SERVICE_NAME`, with the authorized group/environment and OTLP
   traces enabled. Collector uses `host.docker.internal`/host-gateway and
   `/v1/traces`; override `SAGE_AGENT_OTLP_ENDPOINT` only for the actual listener.
   Restrict access to local/container traffic.
4. Configure host-agent stdout/stderr collection. Its OTLP receiver is **trace-only**:
   metrics use Remote Write and logs stay on stdout. Keep backend
   `OTEL_METRICS_EXPORTER=none` and `OTEL_LOGS_EXPORTER=none`; avoid a second
   host-agent scrape of backend metrics. Automatic log/trace linking requires
   parsed `trace_id`/`span_id`; it is not established by console output alone.
5. Validate before enabling forwarding, from repository root:

   ```sh
   python3 infrastructure/observability/check_sage.py
   docker compose --profile sage config --quiet
   docker compose --profile sage run --rm --no-deps otel-collector validate --config=/etc/otelcol/config.yaml
   docker compose --profile sage up -d vmagent otel-collector
   ```

   Then set backend `OTEL_SDK_DISABLED=false` in its private override and recreate
   backend. Its endpoint is `http://otel-collector:4318`; verify actual traces,
   logs and `pql pg_up` / `pql redis_up` in the intended group/environment.
   Keep `sage` enabled for later releases, e.g. `COMPOSE_PROFILES=sage`.
   Disable the backend SDK before stopping forwarding.

Forwarding is not durable replication: federation samples the latest values every
30 seconds with a one-minute lookback, does not backfill outages and loses metric
type metadata. Collector queues are in-memory with bounded five-minute retries;
restarts, full queues and prolonged/authentication failures can lose telemetry.
Local VictoriaMetrics history remains separate. Dashboards, alerts, backfill,
credential rotation and real ingestion acceptance need their own verification.
Sage resource definitions/client/reconciler and actual recorded platform limits
live in [`../iac/sage/README.md`](../iac/sage/README.md).

## Checks

Run from this directory:

```sh
python3 -m unittest discover -s tests -v
python3 check_sage.py
PRODGUILD_OBSERVABILITY_INTEGRATION=1 python3 -m unittest discover -s tests -v
```

The first command runs settings/Compose contract tests; Docker CLI and Compose
are required. `check_sage.py` checks resolved local ingestion settings without
printing tokens; absent settings intentionally fail. It does not contact Sage or
prove group authorization. Docker's normal non-quiet config output contains
resolved credentials: do not publish it.

The opt-in smoke test requires a running Docker engine and image access. It uses
the configured exporter, vmagent, VictoriaMetrics and Collector images with an
isolated PostgreSQL/Valkey, a fake application, and a **local-only** mock Sage
gateway/storage. `OBSERVABILITY_TEST_PYTHON_IMAGE` overrides its Python 3.12
fixture image for approved mirrors. It checks real DB/cache collection, required
Sage labels, metric names/target identity, JWT authorization, trace forwarding
and transient remote-write failure recovery. It never uses real Sage credentials
or sends to Sage. A UUID Compose project isolates its network, ephemeral
loopback ports and volumes; cleanup removes only that test-created project/data.
Do not run production Compose `down --volumes`.
Its fresh PostgreSQL uses the PostgreSQL 18 mount layout and image-default
configuration; the deployment's legacy mount/first-run HBA prerequisites are
documented in the deployment guide. No existing database layout is changed by tests.
