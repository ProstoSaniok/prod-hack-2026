# Same-origin proxy contract

[`Caddyfile`](Caddyfile) is the executable policy:

| Public path | Destination |
| --- | --- |
| `/api`, `/api/*`, `/auth`, `/auth/*` | `backend:8080`, preserving prefixes |
| `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc` | Backend documentation |
| `/metrics`, `/metrics/*`, exact `/healthcheck` and `/ready` | Explicit 404 |
| Everything else | `frontend:80`, including UI deep links |

`/docs-extra`, `/apiculture` and `/authentication` are UI paths.
`/api/healthcheck` is not an alias for `/healthcheck`. Preserve ordinary `handle`:
`handle_path` would strip prefixes. Methods, queries, bodies, headers, JSON errors,
bearer challenges and empty 204s must survive forwarding. An unavailable backend
must yield a proxy error rather than SPA HTML. Body cap is 5,976,883 bytes;
backend probing uses `/healthcheck`.

Operational endpoints remain private. vmagent scrapes backend directly;
root Compose has no backend host port. Local Compose maps `127.0.0.1:18080` for
development. The separate Caddy `:8404` metrics listener
is unpublished in root Compose. Swagger uses root-relative `/openapi.json`.
The Angular dev proxy is configured separately; Caddy does not create CORS access.

## Executable checks

From the repository root:

```sh
python3 -m unittest discover -s tests/integration/proxy -p 'test_fixture.py' -v
python3 tests/integration/proxy/test_proxy.py --caddy /path/to/installed/caddy
```

The second command requires an installed permitted binary and fails explicitly
when unavailable. It does not download Caddy or pull a container. The fixture
binds both HTTP and metrics to distinct IPv4-loopback sockets, disables admin,
persistence and automatic HTTPS, then inspects adapted JSON and launches that
same configuration. Unexpected/wildcard/missing/duplicate listeners fail closed.

Checks cover forwarding, JSON/status/challenge preservation, Swagger paths,
namespace boundaries, encoded/normalized metrics denial, UI fallback and upstream
failure. Upstreams are synthetic: these checks do not establish real application
authorization, Git ACLs, migrations, TLS or deployment health. Historical execution
passed with Caddy 2.11.7 on macOS arm64; use current results for changed configuration.
Only fixture-owned processes and temporary data are cleaned up.

See [deployment](../../../deploy/README.md) and
[observability](../../observability/README.md) for service wiring and telemetry.
