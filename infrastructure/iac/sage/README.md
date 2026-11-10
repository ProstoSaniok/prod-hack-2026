# Sage infrastructure as code

Python 3.12+; standard library only. Run from this directory. No Terraform plugin
installation, package-index access, token in HCL, or credential-bearing state file.

## Layers

- `objects/manifest.json`: active object inventory and dependency graph.
- `objects/dashboards/*.json`: dashboard data, **not** creation scripts.
- `examples/metrics/`: inactive, separate environment/system/collection definitions.
- `sage_iac/resources.py`: object-specific identity, read, create, update, validation.
- `sage_iac/client.py`: bounded HTTP transport, authentication and tenant/group checks.
- `sage_iac/config.py`: definition loading, unique identities and dependency ordering.
- `sage_iac/reconcile.py`: read-only planning, drift comparison, guarded writes and verification.
- `tests/`: offline lifecycle, security, drift and failure-path tests; no real credentials.

## Commands

```sh
just validate
just test
export SAGE_TOKEN_FILE=/absolute/private/path/sage-token
# File must have mode 0600. Alternatively inject SAGE_TOKEN from a secret manager.
just plan
just apply                       # explicit --yes, create/noop only
python3 -m sage_iac apply --yes --allow-update  # only after reviewing existing-object drift
```

`validate` and unit tests never authenticate or contact Sage. CI runs both.
Never place the token on the command line, in a manifest, or in a saved plan.
Remove the environment variable/file after use. Prefer a tenant-scoped service
identity for automation; the supplied user JWT is short-lived. Rotate shared tokens.

The endpoint and tenant are deliberately fixed to
`https://sage.spirit.t-education.com` / **hack-led-03**. A server-side session
check runs before planning and again before applying. Metrics writes additionally
require an assigned group whose returned `tenantKey` matches the authorized tenant.
Do not remove that response check: live group-list filtering returned other tenants
even when a tenant filter was supplied. Such groups are never mutation targets.

### Idempotency and safety

Remote name + tenant (dashboards) or group + natural composite key (MCC objects)
is authoritative; no local state is required. Lists are read on every invocation.
Dashboard lists are paginated. The exact same definitions produce `noop` after
creation, including across fresh processes/checkouts. Only declared fields are
compared, so server-generated IDs/defaults/timestamps do not cause spurious drift.
IDs, secrets and full API responses are not printed by plan.

All resource preconditions are checked before the first write. Dependencies are
ordered and cycles/duplicates rejected. Existing-object updates require explicit
opt-in. Full remote snapshots are re-read before writes; concurrent changes abort
instead of silently overwriting. Every mutation is read back and verified.
Only GET transient failures retry; unknown mutation outcomes must be reconciled
with a fresh plan, never blindly repeated. Redirects cannot forward the token.

There is intentionally **no delete/prune operation**. Removing a definition does
not destroy an object. Renaming creates a new object; review and remove the old
one separately. Reserve `prodguild-` names for this workspace; a matching existing
object is treated as the named resource, but modifying its declared fields still
requires `--allow-update`. Dashboard service membership/layout is not managed or
reset when updating names/viewport fields.

This is not transactional: partial success stays remote and the next plan
reconciles it. Snapshot checks cannot eliminate a race between read and write
without server-side compare-and-swap. Use **one serialized writer per tenant**
(e.g. a protected CI resource group); do not run concurrent applies from machines.
No rollback, distributed lock, automatic import, or Terraform state semantics are
claimed. Keep definitions under code review and protect the apply credential.

## Live result and blockers — 2026-10-16

Created and read back in the authorized tenant:

| Resource | Remote identity |
| --- | --- |
| `dashboard.lotty-stage` / `lotty-stage-overview` | `46e859c5-2070-49a7-bfae-b6c34305e60b` |

A new process running `plan` returned **noop** after creation. Sage dashboards
here are **service-topology maps**, not Grafana-style time-series dashboards.
The map starts empty: no invented services, traces, panels or healthy telemetry
are asserted. Services can be added once real traces provide their identities.

The table above records the historical object created before the project name
was corrected to **prodguild**. Current definitions instead declare
`dashboard.prodguild-stage` / `prodguild-stage-overview`; that object has not been
applied. The additive reconciler will create a separate object on the next
approved apply, not rename or delete the historical map. No remote Sage resources
were changed as part of the naming correction.

The authenticated session has `sage.user` and `sage.private_group_user` for
`hack-led-03`, but **no assigned Sage group**. The metrics API returned
`User has no groups assigned`; no metrics objects were written. Agent discovery
returned no registered agents. Ask the tenant administrator to provision/assign
the tenant's ingestion group and install/register the real host Sage Agent.
Group creation/permission changes are not exposed by the inspected public group
API and are deliberately not guessed or performed on another tenant's group.

After assignment, replace `groupName` in all three inactive metrics definitions
with the actual authorized group, then:

```sh
python3 -m sage_iac validate --manifest examples/metrics/manifest.json
python3 -m sage_iac plan --manifest examples/metrics/manifest.json
python3 -m sage_iac apply --manifest examples/metrics/manifest.json --yes
```

These provision only environment, system, and an **empty** manual scrape
collection; the adapters are tested offline but not live-applied due to the group
blocker. Source collection reads use v2 so `source` round-trips correctly.
No scrape target is registered until the deployment VM/DC and network reachability
are confirmed. Central Sage scraping **cannot** reach a VM's `127.0.0.1:18080`;
use the host agent's local scrape integration or an approved private listener and
firewall. Never expose `/metrics` publicly merely to make provisioning succeed.

Agent registration installs credentials/configuration on a real host; it is not
equivalent to creating an API-only object. No fictitious agent, alert recipient,
host address, group quota or production alert was created. Application telemetry
remains disabled until ingestion is genuinely configured. See the
[observability guide](../../observability/README.md) for the runtime integration contract.

## API contracts and Terraform decision

Verified public documentation:

- `.../documentation/article/api_auth`: Bearer auth.
- `.../documentation/article/group_methods`: read-only public group methods.
- `.../documentation/article/scraping_methods`: MCC environments, systems and collections.

The dashboard lifecycle is the deployed Sage UI's API, not a documented public
Grafana API. Verified against UI release `a10257421f801af02f3d33814cda013ded1d3b5f`:
`utils-DAspTA8u.js` uses `GET /dtracing-graph-api/v2/dashboards`,
`POST /dtracing-graph-api/dashboards`, and
`PATCH /dtracing-graph-api/dashboards/{uuid}`. Creation and listing were tested
live; patch behavior is covered offline, not exercised against existing objects.
If the UI API changes, fail closed and update the adapter/tests after reviewing it.

A custom Terraform provider is **not** included: the suggestion is optional, and
claiming production Terraform CRUD/import/delete behavior for undocumented
dashboard and unavailable group/agent contracts would be misleading. Nor do we
wrap side-effecting scripts in `null_resource`/`local-exec` and call it a provider.
The isolated resource adapters can underpin a real provider later, once stable
contracts, acceptance-test credentials, ownership/deletion policy and state
backend/locking requirements are agreed. Current scope is tested, additive IaC.
