# T01 legacy read-only probe — never a full ACL proof

This document covers only `probe.py`, the original preprovisioned-fixture read
collector. Its placeholder stays deliberately blocked. The now-authorized local
bootstrap and real matrix are documented in [README.md](README.md); their fixture
format is different. The manual checklist below describes broader follow-up
coverage, not the executed coverage of `lab.py`. Unit tests cannot establish live
ACL behavior or release downstream gates.

## Offline reproduction (no credentials, network, or services)

From the repository root:

```sh
cd tests/integration/gitea-spike
python3 -B -m unittest -v test_probe.py
python3 -B probe.py --fixture fixture.example.json
```

The last command emits JSON, reports `fixture.validation` blocked, and exits **2**.
The runner never provisions a service or runs Git. It issues only GET requests
after complete explicit approval. It has no write mode or manual-evidence import.
No dependencies, image pulls, admin bootstrap, or guessed image tags are needed.

Exit codes: **1** if an observed disclosure is detected; otherwise **2** (blocked).
There is deliberately no successful isolation exit code: unrun manual evidence
always keeps `overall=blocked` and `isolation_proven=false`, even if every automated
row passes. Each fixed matrix row has `passed`, `failed`, `blocked`, or
`manual-required`. A passed row means **only its named observation**; e.g. a static
retained-ref snapshot is not GC/restart or write-protection proof. Unexpected
identity/version/schema/control failures are blocked, never access-denial passes.
`fixture_sha256` binds output to the canonical validated non-secret fixture JSON
(sorted keys, compact separators), not to credentials or an image attestation.

## Authorization and fixture preparation — preprovisioned remote probe

For a future remote run obtain written authorization identifying:

- Exact supported stable Gitea release, approved registry image **by verified
  digest**, platform/architecture, availability/pull evidence and support source.
  Obtain deployment/operator attestation that the running service uses that image;
  `/api/v1/version` cannot attest an image digest. No default image is provided.
- One exact origin without path/query/fragment/userinfo or trailing slash, normally
  `https://<approved-host>[:<approved-port>]`, with a trusted valid certificate.
  Internal and external origins, reverse proxies and optional SSH need separately
  scoped evidence. This runner approves **one origin** and assumes root mounting.
- A disposable organization `prodguild-t01-<8-to-24-lowercase-alphanumerics>` and
  six actual, **distinct, non-admin** account names and numeric IDs: `backend`,
  `organizer`, `student_a`, `student_b`, `reviewer`, `expert`. These are Gitea
  accounts, not application bearer tokens or inferred SSO identities.
- Exactly the six token **environment-variable names** in the fixture. Values
  must be supplied securely and remain outside JSON, Git, shell history, process
  arguments, terminal output and reports. Never inspect arbitrary local secrets.
- Separate permissions for fixture creation, read probes, destructive test writes,
  server GC/restart, and final cleanup. Read-probe approval does not grant writes.

Have an authorized operator preprovision the fixture using the approved release's
UI/API documentation. This is a recipe, **not an instruction to bootstrap an
admin on an actual service now**:

1. Provision the six scoped accounts, record IDs from authenticated `/api/v1/user`,
   and create the disposable organization. Backend may own **only this test
   organization**; no tested account may be a global administrator. Disable open
   registration, Actions, webhooks and unneeded execution features for the spike.
   Record bootstrap identity/trust boundaries, mapping/revocation procedure and
   actual SSH availability; never assume an SSH endpoint or application login.
2. Create six **separate private repositories**, IDs/names as in the fixture. Use
   explicit repository teams/collaborators, not organization-wide read teams,
   automatic all-repository access, public mirrors or shared credentials. Verify
   effective inherited memberships with the operator. Suggested minimum matrix:

   | Repository | backend | organizer | student_a | student_b | reviewer | expert |
   | --- | --- | --- | --- | --- | --- | --- |
   | starter | write | write | read | read | read | read |
   | reference | write | write | deny | deny | deny | deny |
   | draft_case | write | write | deny | deny | deny | deny |
    | solution_a | write | deny | read | deny | read | read |
    | solution_b | write | deny | deny | read | read | read |
    | pending | write | deny | deny | deny | read | read |

   This is an isolated compatibility fixture, **not an accepted production topology**.
    Participant native code access is read-only. Incidental PR comments/reviews
    are untrusted annotations, never canonical grades/grants. Useful per-project
    PR links are allowed with account-specific read grants. Only backend native
    writes are suggested here. Same-credential
   allowed-write controls use separately authorized disposable scratch repositories,
   not authoritative submitted refs. Global platform admins
   remain a trusted infrastructure boundary, not organizer/academic capabilities.
   Reference/draft/pending content must never be hidden branches in student-readable
   repositories. No solution PR may target or merge into shared starters.
3. Generate a unique non-secret marker per repo matching
   `PRODGUILD_T01_[A-Z0-9_]{8,96}`. In starter/reference/draft_case/pending put its
   marker in `probe.txt` on `main`. The pending marker is the **pending sentinel**:
   keep it out of all student-readable files, commits, PRs, reviews and metadata.
   Use synthetic draft/quality feedback only; do not put actual grades/data here.
4. In each solution repository create `review-base`, a submitted commit modifying
   `probe.txt` to include its solution marker, and an **intra-repository** PR from
   a backend-controlled retained branch to `review-base`. Seed the PR body, one
   issue comment, one published **synthetic** review, and one inline review comment
   with that solution marker. Record the PR number and review ID. The PR diff must
   include the marker. Pending real feedback is not this seeded published review.
5. Protect each retained branch with the exact approved release's rules. Only the
   backend may create retained refs; forbid student update/delete/force-push via
   both Git and API, including PR web actions. Record exact rule fields and
   effective push/force/delete permissions. Create the retained ref at the submitted
   SHA, then move `work` to a **different** SHA without moving the retained PR head.
   Record both SHAs and branch names. Do not merge the solution PR. The read runner
   observes this snapshot but cannot prove how it was made or how long it survives.
   The candidate contract also retains/protects the exact base commit B under
   `refs/heads/prodguild/bases/<submission_id>` and head C under
   `refs/heads/prodguild/submissions/<submission_id>`. Both slash-containing ref
   patterns require separately recorded manual API/web/Git/GC/restart evidence;
   the small runner fixture uses simple branch names and does not prove those rules.
6. Fill a **local scratch copy** of `fixture.example.json`, replacing every null
   with the approved literal. IDs come from actual responses, never fabricated IDs.
   Keep each repo name/ID/marker, account name/ID/token-env distinct. Unknown keys
   (including token/password fields), duplicate JSON keys, mutable image tags,
   traversal names and inconsistent branch/commit snapshots are rejected.

Tokens: use separate, short-lived, revocable, non-admin Gitea PATs for all six
accounts. For the read runner enable the approved release's `/user` identity read
and repository/PR/issue read scopes for **all repositories accessible to that
account**. No admin/user-management/token-creation scopes, repo-specific token
restrictions that could masquerade as ACL denial, or public-scope-only tokens.
Determine exact scope names from the selected release's token UI/OpenAPI; they are
not guessed here. Future write tests require separately approved write scopes and
successful allowed-write controls with the **same credential** before interpreting
a rejected protected write. Issue-only/repo-only or read-only tokens cannot prove
all needed native Git/API write restrictions. Never use production credentials.

## Approved read-only run

Keep the fixture/evidence outside Git in an operator-owned mode-0700 scratch
directory (for example a dedicated directory under the approved OpenCode temporary
root). Copy the example there and edit non-secret values. `FIXTURE` is that absolute
path. Do not enable shell tracing, Python HTTP debugging, request logging or Git
tracing. Prefer a secret manager injecting only the six named variables into the
probe process. For a local terminal, this hidden prompt method avoids token values
in shell arguments/history (POSIX-compatible shell):

```sh
set +x
umask 077
export FIXTURE='<absolute-scratch-path>/fixture.json'
python3 -B - "$FIXTURE" <<'PY'
import getpass, json, os, subprocess, sys
from pathlib import Path
import probe
cfg = probe.load_fixture(Path(sys.argv[1]))
env = {"PATH": "/usr/bin:/bin", "HOME": str(Path(sys.argv[1]).parent)}
for role in probe.ROLES:
    env[cfg["accounts"][role]["token_env"]] = getpass.getpass(role + " scoped token: ")
# Inspect these literal approvals against the written authorization, not merely
# the fixture. Replace all placeholders; do NOT derive approvals from JSON.
command = [sys.executable, "-B", "probe.py", "--fixture", sys.argv[1],
    "--execute-read-only", "--approval-reference", "<approved-read-ticket>",
    "--approve-origin", "https://<approved-host>",
    "--approve-release", "<approved-x.y.z>",
    "--approve-image", "<approved-registry/path>@sha256:<verified-64-hex-digest>",
    "--approve-namespace", "prodguild-t01-<approved-disposable-nonce>",
    "--approve-account", "backend=<approved-backend-login>",
    "--approve-account", "organizer=<approved-organizer-login>",
    "--approve-account", "student_a=<approved-student-a-login>",
    "--approve-account", "student_b=<approved-student-b-login>",
    "--approve-account", "reviewer=<approved-reviewer-login>",
    "--approve-account", "expert=<approved-expert-login>",
    "--approve-token-env", "backend=PRODGUILD_T01_BACKEND_TOKEN",
    "--approve-token-env", "organizer=PRODGUILD_T01_ORGANIZER_TOKEN",
    "--approve-token-env", "student_a=PRODGUILD_T01_STUDENT_A_TOKEN",
    "--approve-token-env", "student_b=PRODGUILD_T01_STUDENT_B_TOKEN",
    "--approve-token-env", "reviewer=PRODGUILD_T01_REVIEWER_TOKEN",
    "--approve-token-env", "expert=PRODGUILD_T01_EXPERT_TOKEN"]
# Inherit no arbitrary secrets into the probe if using a managed environment.
# Output is sanitized matrix JSON, not upstream bodies or credential-bearing URLs.
with open(Path(sys.argv[1]).parent / "read-evidence.json", "x", encoding="utf-8") as out:
    result = subprocess.run(command, env=env, stdout=out, check=False)
print("probe exit:", result.returncode, "(1=disclosure, 2=blocked)")
PY
```

Run this from `tests/integration/gitea-spike`. Set `umask 077` beforehand. The token
values travel in the child environment, never argv or fixture; the harness does
not spawn further children. A dedicated operator session/OS secret boundary is
still required. Remove the six variables or terminate that session when finished.
If using injected variables instead, run the same `probe.py` command directly with
literal approvals. Approval flags must match **all** identities, token-env names
and literals; the
ticket is an acknowledgement, not cryptographic evidence of authorization.

HTTP is refused except with `--allow-loopback-http` **and** a literal loopback IP
origin (`127.0.0.1`/`[::1]`, not `localhost` or private-network DNS). That switch also
has to be used when loading a local HTTP fixture in the prompt wrapper. Use only
disposable credentials on explicitly approved isolated loopback; it proves no
production TLS property. Certificate validation is never disabled. No redirects,
even same-origin redirects to login, are followed. Explicit origin credentials,
paths, percent-encoding, query strings and fragments are rejected.

Every HTTP request has a 5-second socket **and Unix hard wall deadline**, including
DNS/handshake/body read, and a 256-KiB body cap, including error bodies. Compression
is refused; no retries, cookie jar, inherited proxy, `.netrc` or auth-helper lookup.
Ambient `SSLKEYLOGFILE` does not enable TLS key logging in the probe.
The runner requires Unix `setitimer` on the main thread and refuses unsupported
execution environments instead of weakening the deadline. With this fixed matrix
there are at most 500 requests, sequentially (at most about 42 minutes if every
request consumes its full deadline). A fixture larger than 64 KiB is refused.
Large/paginated/unexpected responses are blocked, not truncated into a pass.

## What the automated rows actually observe

- Exact API release comparison (not registry availability or image attestation).
- `/api/v1/user` exact numeric ID/login and `is_admin=false` for every credential,
  followed by an allowed **private** repo control for that same account.
- Owner-positive metadata/private flag, refs and seeded file for every fixture
  repo; solution PR, diff, issue comments, reviews and review comments. Seeded
  artifacts must be present on page 1 (limit 50); this is a small disposable fixture,
  not an exhaustive pagination scanner.
- Denials for reference/draft to both students/reviewer/expert, each cross-student
  solution, and pending to organizer/both students. For **each endpoint**, the
  denied actor's `/user` and allowed-private-repo and the real owner's same resource
  must succeed before **and after** a JSON 403/404. A 401, redirect, HTML error,
  timeout, missing seeded object or outage is blocked. A 2xx for a forbidden actor
   fails, even if its body is malformed. Controls use real distinct credentials in
   a future live run; the unit-test simulator is not a live proof.
   A403/404 containing a known forbidden private marker also fails, including
   JSON-escaped or nested base64 content. An HTTP denial status is not enough.
   Forbidden-marker policy covers the full fixture ACL table, including organizer's
   forbidden solutions, not only endpoints covered by automated denial requests.
- Submitted/retained and moved-work-branch SHA snapshots and commit readability;
  unmerged intra-repo PR head and seeded review must target the submitted commit.
- After a positive private sentinel-file control, bounded absence checks in the
  listed student-owned API surfaces and starter views read as **both** students.
  Base64 file content is decoded for this check. Absence is only a bounded snapshot.

Web authentication is **not** assumed to accept API PAT headers, and an HTTP Git
advertisement is **not** treated as a clone. All web, HTTPS clone, arbitrary raw
links/ref fetches/history, fork topology and write/restart rows remain manual.
No Git process is started by `probe.py`, so it cannot inherit a helper/hook or put
clone tokens in argv. Server access logs and an untrusted authorized endpoint are
outside the sanitized-output guarantee: configure the operator's logging properly.

## Broader remote/manual checklist (not an executed-results inventory)

Record each operation's actor ID, exact pinned release/image attestation,
credential-free URL, request type, bounded outcome, before/after control outcomes,
immutable object IDs, expected/observed result and sanitized evidence identifier.
Keep secrets/raw responses out of reports. Record **blocked**, not passed, for
outage, scope mismatch, unknown API shape, unknown SSH availability, untested
required capability,
ambiguous response or missing positive control. Supplementary manual evidence is
reviewed by the task lead/independent reviewer; this runner never imports it or
automatically promotes T01 acceptance.

### Credential-safe manual HTTP and Git command patterns

The following commands are templates for an operator **after separate write/read
authorization**. These templates are not the executed `lab.py` commands. Use literal paths/names from the
approved fixture and a new private response file per request. Do not type a token
into any command. This separate manual helper validates before launching curl and
passes its header through stdin, not `-H 'Authorization: ...'` or a credential URL:

```sh
set +x
umask 077
# Only after the ticket authorizes this exact method/path/payload/fixture:
export PRODGUILD_T01_MANUAL_APPROVAL='<separate-manual-approval-ticket>'
# Inject the six named token variables securely. '-' means no request body.
python3 -B - "$FIXTURE" '<role>' '<METHOD>' \
  '/api/v1/repos/<approved-namespace>/<approved-repo>/<approved-resource>' \
  '<scratch-payload.json-or-dash>' '<new-scratch-response.json>' <<'PY'
import os, pathlib, re, subprocess, sys
import probe
fixture_path, role, method, path, payload, output = sys.argv[1:]
ticket = os.environ.get("PRODGUILD_T01_MANUAL_APPROVAL", "")
probe.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,95}", ticket) is not None)
cfg = probe.load_fixture(pathlib.Path(fixture_path))  # HTTPS required here
probe.require(role in probe.ROLES and method in ("GET", "POST", "PUT", "PATCH", "DELETE"))
probe.require(re.fullmatch(r"/api/v1/[A-Za-z0-9_./?=&-]+", path) is not None
              and ".." not in path and "//" not in path)
probe.require(path.startswith("/api/v1/repos/" + cfg["namespace"] + "/")
              or (method == "GET" and path in ("/api/v1/user", "/api/v1/version")))
scratch = pathlib.Path(fixture_path).resolve().parent
destination = pathlib.Path(output).resolve()
probe.require(destination.parent == scratch and not destination.exists())
command = ["/usr/bin/curl", "--disable", "--config", "-", "--proxy", "", "--noproxy", "*",
    "--proto", "=https", "--max-redirs", "0", "--connect-timeout", "3", "--max-time", "5",
    "--max-filesize", "262144", "--silent", "--request", method,
    "--header", "Content-Type: application/json", "--output", str(destination),
    "--write-out", "%{http_code}"]
if payload != "-":
    source = pathlib.Path(payload).resolve()
    probe.require(source.parent == scratch and source.is_file() and source.stat().st_size <= 262144)
    command += ["--data-binary", "@" + str(source)]
command.append(cfg["origin"] + path)
token = probe.credentials(cfg, os.environ)[role]
try:
    result = subprocess.run(command, input='header = "Authorization: token ' + token + '"\n',
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "HOME": str(scratch)}, timeout=8, check=False)
    status = result.stdout.strip()
    print("curl_exit:", result.returncode, "http_status:", status if re.fullmatch(r"[0-9]{3}", status) else "blocked")
except subprocess.TimeoutExpired:
    print("blocked: manual request timeout; reconcile ambiguous writes before retry")
PY
```

The header goes **only to curl's stdin**, not terminal/evidence. Do not add `tee`,
`-v`, `-L`, `--trace`, `--insecure`, a
proxy or credentials in the URL. `curl --disable` must be its first option to
disable `.curlrc`. Use a curl version enforcing streamed `--max-filesize`, or use
an equivalently bounded trusted client. Inspect private response files locally,
record only allowlisted status/IDs/SHAs, and do not publish raw headers/bodies.
HTTP 000, redirects, curl timeout/size/transport failures are blocked. This manual
helper intentionally cannot bootstrap accounts/organizations/repositories; use
the separately authorized operator UI/client for those management operations.

For a real HTTPS clone use a fresh repository and a clean child environment.
This example **only clones**; it suppresses child output, disables helpers/hooks,
templates, inherited Git config/proxies/trace/SSH, and supplies a single account's
token via an ephemeral askpass IPC channel (not URL/argv). Run it once for that
account's own allowed private repo, then the forbidden target, then repeat both
actor and owner controls. A nonzero Git exit **alone is not a denial proof**.

```sh
python3 -B - "$FIXTURE" '<role>' '<repository-key>' <<'PY'
import os, pathlib, signal, subprocess, sys, tempfile
import probe
cfg = probe.load_fixture(pathlib.Path(sys.argv[1]))
role, key = sys.argv[2:]
assert role in probe.ROLES and key in probe.REPOS
account = cfg["accounts"][role]
token = probe.credentials(cfg, os.environ)[role]
url = cfg["origin"] + "/" + cfg["namespace"] + "/" + cfg["repositories"][key]["name"] + ".git"
with tempfile.TemporaryDirectory(prefix="prodguild-t01-git-", dir=pathlib.Path(sys.argv[1]).parent) as home:
    helper = pathlib.Path(home) / "askpass"
    helper.write_text("#!" + sys.executable + "\nimport os,sys\np=sys.argv[1]\n"
        "if p.startswith('Username'): print(os.environ['PRODGUILD_GIT_USERNAME'])\n"
        "elif p.startswith('Password'): print(os.environ['PRODGUILD_GIT_PASSWORD'])\n"
        "else: sys.exit(1)\n")
    helper.chmod(0o700)
    env = {"PATH": "/usr/bin:/bin", "HOME": home, "XDG_CONFIG_HOME": home,
        "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": str(helper),
        "PRODGUILD_GIT_USERNAME": account["username"], "PRODGUILD_GIT_PASSWORD": token}
    prefix = ["/usr/bin/git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
        "-c", "init.templateDir=", "-c", "protocol.file.allow=never",
        "-c", "protocol.ext.allow=never", "-c", "http.followRedirects=false",
        "-c", "http.sslVerify=true"]
    def safe_git(arguments):
        child = subprocess.Popen(prefix + arguments, cwd=home, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            code = child.wait(timeout=30)
            print("git_exit:", code, "(not an ACL verdict)")
            return code
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            print("blocked: git timeout")
            return None
    safe_git(["clone", "--bare", "--no-local", "--no-hardlinks", url, "evidence.git"])
    # No write commands here. Future authorized operations use this SAME wrapper,
    # fresh repo, credentials and credential-free URL; see exact argument rows below.
PY
```

Only askpass's pipe to Git carries its response; never log that pipe or environment.
Use an independently trusted Git executable; adjust its **approved absolute path**
if `/usr/bin/git` is unavailable. This wrapper gives sanitized exit observations,
not protocol-error classification. Obtain bounded, sanitized server-side HTTP
authorization evidence for denied clones; if that evidence or positive controls
are unavailable, keep clone blocked. No `git://`, SSH fallback, submodules, LFS,
checkout filters, global credential stores or arbitrary cloned hooks are needed.
For retention checks keep the temporary bare repo alive until the authorized steps
finish; its default cleanup only removes this newly allocated disposable directory.
The Git example bounds time, not pack size: the operator must provide a disposable
filesystem quota and object/byte limits before cloning or scanning history. Do not
run it against arbitrary repositories; keep clone evidence blocked without those
resource controls.

### Required experiments and precise operations

Before **every denial** repeat `/user`, same-account allowed private access, and
owner access to the exact target before/after; before a write denial also prove a
same-account allowed write in an unrelated disposable scratch branch/file. Confirm
the selected release's bundled OpenAPI supports the following operations/fields;
record schema differences as blocked and adapt only after review. No endpoint is
being claimed compatible merely because it appears in this checklist.

API paths below omit the `/api/v1` prefix for readability; include it in requests.

1. **Web and clone/direct-link isolation.** Use distinct browser profiles with
   actual organizer/student_a/student_b/reviewer/expert sessions; confirm profile
   identity and own private repo first. Test each forbidden repo, `/src/branch/...`,
   `/src/commit/<submitted-sha>/...`, `/raw/commit/<sha>/probe.txt`, `/pulls/<n>`,
   `.diff`, `.patch`, comments/reviews, known refs/SHAs and archive URLs as the
   unauthorized actor and anonymous user. Login redirects are not denial evidence
   without identity/control checks. Repeat with the HTTPS clone wrapper (actual
   fetch, not just `/info/refs`). Fetch a known other student's retained SHA/ref
   explicitly into a fresh private scratch clone: Git arguments
   `['-C', 'evidence.git', 'fetch', url, retained_ref_or_sha]`. Owner fetch must work;
   forbidden fetch/clone must demonstrably be authorization denial, not network
   failure. Record separate internal/external origins and SSH absent/available.
2. **All Git pending/draft/reference paths.** Confirm the sentinel exists as
   backend in pending storage. Fetch **all accessible advertised refs/tags**, PR
   refs, old commits/known hashes and archives from each student's own solution and
   starter; enumerate reachable objects (`rev-list --objects --all`, inspect blobs
   with `cat-file`) under size/time limits and search for all private markers.
   Try private known SHA fetch/raw/compare links, API search/activity/notifications,
   PR/review/comment histories and deleted/closed PRs too. Never publish the pending
   marker to a student PR just to hide it later: Git history retains it. Absence
   checks over a bounded subset do not establish universal absence; record coverage.
3. **Stale blob concurrency.** Create a separate scratch file `concurrency.txt`,
   read `GET /repos/{owner}/{repo}/contents/concurrency.txt?ref=work` and save blob SHA `S0`.
   Send `PUT` to the same contents path with JSON
   `{"branch":"work","sha":"S0","content":"<base64-A>","message":"<unique-op-A>"}`.
   Verify its new blob/commit and content. Send another PUT with **S0**, different
   base64 B and unique marker B. It must reject stale SHA (record actual 409/422 or
   release-specific result, not a guessed status); re-read must still show A.
   Repeat DELETE contents with stale `sha` and verify no deletion. Run simultaneous
   same-SHA writers; at most one wins. Distinguish file blob SHA from commit SHA.
   Creating a new file uses POST contents with no `sha`; deleting uses DELETE
   contents with `branch`, `sha`, `message`. Do not mutate the seeded probe artifact
   during a read-evidence run; use another authorized fixture phase or restore and
   re-record the fixture afterward.
4. **Protected ref movement/deletion, API and Git.** GET
   `/branch_protections/{retained-branch}`, `/branches/{retained-branch}` and
   `/git/refs/heads/{retained-branch}` as backend and record effective rule JSON.
   For each student credential prove an allowed work-branch/file write, then attempt
   API DELETE `/branches/{retained-branch}`, and an API contents PUT/DELETE targeting
   that retained branch at the **current valid** blob SHA. Also attempt creating a
   colliding branch with POST `/branches`
   `{"new_branch_name":"<retained>","old_ref_name":"<other-commit>"}`; it must not
   replace the existing ref. Gitea may expose no generic Git-ref PATCH/DELETE API;
   explicitly record unsupported methods, not a fake successful protection test.
   Check any release-supported ref update/delete endpoint from its OpenAPI too.
   With the clean Git wrapper and a fetched authorized alternative commit, attempt
   `['-C','evidence.git','push','--force',url,'<other-sha>:refs/heads/<retained>']`
   and `['-C','evidence.git','push',url,':refs/heads/<retained>']` (separate actions).
   Test regular fast-forward movement as well, not only force-push. All student
   attempts must fail and backend re-reads/fetches must still resolve exactly to the
   submitted SHA. Test UI/API PR update/merge paths cannot bypass that protection.
   Never destroy the only retained copy. Backend mutation/deletion tests need
   **additional** authorization and a separate sacrificial retained ref; document
   backend application policy even if the privileged service can administratively
   bypass protection. The read runner cannot prove backend policy or immutability.
5. **Branch movement, GC and restart retention.** Record submitted SHA/tree/file
   hash/PR head and retained-ref target. Move the ordinary work branch twice,
   including an authorized force-push to unrelated history, and delete obsolete
   scratch branches. Use `push --force` only for the specifically authorized work
   branch. Then the operator performs the approved release's maintenance GC on the
   **disposable repositories** and restarts the isolated service (never a production
   VM or broad volume deletion). Save maintenance/restart completion evidence.
   Re-run API branch/commit/raw/tree and clean Git fetch checks: retained ref,
   reviewed commit/tree and PR head must remain the original submitted SHA, while
   work HEAD differs. A surviving SHA immediately after a push is not GC proof.
6. **Private-fork/upstream PR leakage.** In separately approved sacrificial copies,
   POST `/repos/{owner}/{repo}/forks` with explicit namespace/name and verify actual
   `private`, parent/source IDs, collaborators, inherited teams and writable refs.
   Fork as student_a, add its private marker, and POST an upstream PR using that
   release's documented fork-qualified `head` and `base`. Test student_b/anonymous
   web, API metadata, refs, PR list/detail/diff/patch, issue/review comments, reviews,
   raw known SHA fetch, clone and events against both upstream and fork. Include
   positive owner and student_b own-repo controls. No merge. A 404 for a nonexistent
   PR proves nothing: first confirm it actually exists as owner. If any surface
   leaks, reject shared-upstream topology; repeat the full matrix using isolated
   per-project repositories and intra-repo PRs. A private checkbox alone is not
   private inheritance proof. No switch to globally public demo repos is allowed.
7. **Review positions and file fallback.** GET `/pulls/{n}/files` and diff, record
   base/head IDs and hash of the exact pinned diff. On a scratch PR containing
   additions/deletions/renames/binary/empty files use POST `/pulls/{n}/reviews`
   with the release-supported `commit_id`, `event`, `body`, and `comments` shape
   (commonly `path`, `body`, `old_position`/`new_position`; verify, do not assume).
   Confirm returned comment ID/path/side/line maps to the **pinned** diff in UI/API.
   Test old/new sides, out-of-range, stale commit/diff, deleted lines, no diff and
   binary paths. Invalid or unsupported positions must not silently attach to a
   different line. If native file-only comments are unsupported, POST a safe general
   issue comment `/issues/{n}/comments` with an escaped path and stable commit/diff
   metadata; retain file association in future DB metadata, not invented native
   capabilities. Test HTML/Markdown injection escaping and no secret URLs. Pending
   feedback stays in private pending storage until confirmation; test `needs_revision`
   and unconfirmed results remain invisible through **Git**, not just application UI.
8. **Ambiguous writes and lookup/recovery.** For each branch/file/fork/repo/PR/review/
   comment creation use a stable unique operation marker and save intent first.
   In an authorized fault-injected proxy, drop the **response after upstream
   commit**, not before sending. On timeout do **not** repeat creation. Lookup by
   deterministic namespace/name; branch ref/SHA; contents blob/commit plus message;
   PR base/head/marker; or paginated issue-comments/reviews and stored marker/author/
   commit. GET the candidate's ID and validate the complete intended payload. Zero
   matches requires reconciliation/backoff; >1 is a conflict, never arbitrary first
   match. Test payload mismatch and crash/restart between lookup and local ID save.
   Record which selected-release operations demonstrably support request idempotency
   and which require application journal/deduplication. Do not assume an
   `Idempotency-Key` header is honored merely because a server accepts it. Keep all
   GET lookups side-effect-free. Fault injection and actual writes require their
   own approval and are not performed by this runner.
9. **No solution merge and cleanup.** Compare starter refs/tree/log before and
   after every phase and verify no solution/quality/reference marker entered them.
   Capture final ACL/token scope inventory and revoke all six disposable tokens.
   Only after evidence review and explicit cleanup approval remove the exact
   disposable namespace/accounts/volumes created for this spike. Never delete
   existing user work or use broad recursive cleanup on a service/workspace.

Manual rows are intentionally still unrun after following only the automated run.
Acceptance additionally requires the actual complete isolation/compatibility
matrix, independent security review, image/support evidence and T01 contract gate.
