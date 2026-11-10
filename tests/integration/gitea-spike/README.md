# T01 disposable local Gitea lab

`lab.py` bootstraps and exercises a real **official Gitea 28.1.0** on loopback,
with isolated SQLite/Git storage and synthetic actors. The historical owner scope
authorized that local experiment, including writes, maintenance and cleanup;
reproduction needs its own authorized disposable-service scope.
The recorded owner-run corrected matrix passed its66 named rows on Python3.12.15:
[corrected evidence](evidence/local-matrix-correction.json).
the [earlier matrix](evidence/local-matrix.json) is preserved as historical evidence.
Offline tests are harness/contract checks only. These are historical results,
not a claim that checks were rerun during documentation relocation.

The application now implements private Git-backed publication/projects/submissions,
reviews, mentoring, progression and economy, with active backend-backed Angular routes.
Native account/link provisioning and binary files remain unfinished. See the current
[architecture](../../../architecture.md#gitea-identity-and-isolation) and
[backend guide](../../../src/backend/README.md). The generated candidate retains its
historical unfrozen gate metadata; it is not an implementation inventory or runtime schema.

Coordinator review of `7d991ce` held full security-evidence acceptance: its native
substring checks and incomplete decoded-marker checks could falsely pass. Old66/66
labels remain historical, not renewed confidentiality proof. The corrected evidence
comes from fresh fixture `prodguild-t01-lab-bf59da5559` after targeted independent
review. It records exact native-denial classifications and bounded actor-specific
decoded-marker policy on inspected bodies; it is not exhaustive security proof.
Independent reviewers inspect output, not the live service. Two earlier corrected
attempts failed closed on diagnostic/scanner compatibility and remain separate
artifacts. All three owned resource sets were removed and absence verified in
[cleanup evidence](evidence/local-cleanup-correction.json).
Corrected-source publication at `397ae3cfc8cbff20ef894257156e2b013d25839e`
accepted bounded evidence, not full T01/production acceptance. The coordinator's
204 offline checks and inspection of 66 corrected rows were distinct from the
owner's live run; intermediate 44/66 and 65/66 attempts remain preserved.

## Reproduce from repository root

Requires Docker, `/usr/bin/git`, Python 3.12+ and a unique unused state directory.
The committed [release lock](release.lock.json) records verified official release,
support and registry sources. Reconfirm current security support before a later run.
This public distribution is authorized for this disposable lab only, not Spirit
production mirror availability or VM deployment approval.

```sh
python3 -B tests/integration/gitea-spike/lab.py bootstrap --state artifacts/prodguild-t01-lab-repro --release-lock tests/integration/gitea-spike/release.lock.json
python3 -B tests/integration/gitea-spike/lab.py run --state artifacts/prodguild-t01-lab-repro --evidence artifacts/T01-reproduced-matrix.json
python3 -B tests/integration/gitea-spike/lab.py cleanup --state artifacts/prodguild-t01-lab-repro
```

Bootstrap includes the official digest-pinned pull, using the same verified local
Unix daemon as startup and cleanup. Docker routing environment overrides and remote
contexts are rejected before mutation. Do not substitute a bare ambient `docker pull`.

Run the matrix once per fresh bootstrap: its destructive synthetic probes are not
an idempotent fixture refresh. Preserve sanitized evidence before cleanup. Exit0
means the named local observations passed; it does not freeze the contract or
approve production. Exit1 means a matrix row failed; exit2 means a harness blocker.
Use a different fresh state path after an interrupted bootstrap; clean only its
owned resources. Never run Docker prune or remove existing services/volumes.

All test passwords/tokens live only in ignored `artifacts/<run>/fixture.local.json`
(0600), under a0700 directory. Generated Gitea instance secrets remain in its
isolated volume (app.ini0600). HTTP cookies stay in memory. No credentials in URLs,
Git argv, reports or output. Git uses a clean environment, askpass, disabled global
helpers/hooks/config, bare clones and no file/ext protocol. Raw errors remain in
the ignored private run directory. Do not print or commit those files.

Resources have unique `prodguild-t01-lab-*` names and an exact ownership label.
Only `127.0.0.1:<free-port>:3000` is published. SSH is unpublished/disabled in Gitea;
the image's bundled sshd may listen internally, which proves no SSH integration.
A dedicated bridge has no other services; it is not an egress firewall. Actions,
registration, mail and Git hooks are disabled. No Docker socket or host checkout
is mounted. Container memory/CPU/PID limits and small generated fixtures bound the
experiment; this is not an arbitrary-repository load tester.

## Actual coverage and interpretation

- Six distinct non-admin Gitea identities and a separate bootstrap administrator.
- API, real cookie-authenticated web/raw/archive, actual native HTTP clone/fetch
  denial for references, drafts, pending canary and peer work, with positive controls.
- Authorized credential-free repository/PR links; distinct login cookies, no SSO
  assumption. HTTPS/TLS, reverse proxy and SSH remain production prerequisites.
- Same-actor allowed writes plus denied protected base/head regular/force pushes,
  deletion, API edits, rule changes and PR merge; cookie web edit/delete denial.
- Work diverges from C; retained B/C survive actual reflog expiration, Git GC and
  container restart with identical pinned diff hashes.
- All advertised refs and reachable fixture blobs scanned; known peer SHA denied,
  API list/search and web explore scoped. Pending drafts are not projected into
  learner repositories. This is not a test of future application PostgreSQL logic.
- Actual concurrent stale blob writes; Gitea can return403 for ref-lock conflict
  and422 for stale blob. Adapters must distinguish those from ACL errors and
  return business409 safely; this fixture does not test current application mapping.
- Private fork inheritance and **observed shared-upstream PR diff disclosure**.
  That variant is rejected; isolated per-project same-repository PRs are selected.
- Published pinned old/new line annotations and general file-comment fallback.
  Native file-level comments remain unsupported in the application contract.
- Actual response dropped after successful repo/PR/review creation; lookup recovers
  one object without blindly repeating POST. Duplicate native Idempotency-Key
  comments demonstrate that application dedup cannot be delegated to Gitea.
- Incidental student comments succeed, but editing/deleting backend-authored
  projections fails. They are untrusted annotations, never application grades,
  grants, confirmations or reward commands. PostgreSQL is authoritative.

The executed row inventory is authoritative. The broader historical
[remote/manual checklist](READ_ONLY_PROBE.md) is not a claim of full exhaustive
surface testing. The optional `probe.py` accepts a different preprovisioned fixture,
is strictly read-only and always retains manual-required gates; its placeholder
still exits2. It is not used to label this lab passed.

## Offline checks

```sh
uv run --no-project --python 3.12 --with openapi-spec-validator==0.7.2 python -B -m unittest discover -s tests/integration/gitea-spike -p 'test_*.py'
uv run --no-project --python 3.12 --with openapi-spec-validator==0.7.2 python -B tests/integration/gitea-spike/check_contract.py
uv run --no-project --python 3.12 --with ruff==0.12.12 ruff check --select E4,E7,E9,F,I tests/integration/gitea-spike
uv run --no-project --python 3.12 --with ruff==0.12.12 ruff format --check --line-length 120 tests/integration/gitea-spike
```

See [contract reproduction and historical acceptance](CONTRACT.md),
[topology](../../../architecture.md#gitea-identity-and-isolation) and the retained
evidence JSONs for release/runtime, correction provenance and production proof limits.
