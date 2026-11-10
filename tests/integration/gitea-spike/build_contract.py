"""Reproduce the historical NOT-FROZEN candidate, not today's runtime OpenAPI.

Candidate-era descriptions/gates remain historical; current implementation is
documented in architecture.md and src/backend/README.md. Preserve the v0.7 input.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "prodguild-openapi-v0.8-rest.json"
METHODS = {"get", "post", "put", "patch", "delete"}


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def string(**kwargs):
    return {"type": "string", **kwargs}


def enum(*values, **kwargs):
    return string(enum=[*values, None] if kwargs.get("nullable") else list(values), **kwargs)


def integer(minimum=0, **kwargs):
    return {"type": "integer", "minimum": minimum, **kwargs}


def number(**kwargs):
    return {"type": "number", **kwargs}


def array(items, **kwargs):
    return {"type": "array", "items": items, **kwargs}


def obj(properties, required=None, **kwargs):
    result = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        **kwargs,
    }
    required = list(properties) if required is None else required
    if required:
        result["required"] = required
    return result


def response(name, description="Успешный результат", location=None):
    value = {"description": description, "content": {"application/json": {"schema": ref(name)}}}
    if location:
        value["headers"] = {"Location": {"schema": string(), "description": location}}
    return value


UUID = string(format="uuid")
TIME = string(format="date-time")
BOOL = {"type": "boolean"}
SHA = string(
    pattern="^[0-9a-f]{40}$", description="Full SHA-1 object ID on the approved Gitea release; never a moving ref."
)
VALUE = number(minimum=0, maximum=100)
DECIMAL = string(
    pattern=r"^-?[0-9]+(?:\.[0-9]+)?$", description="Exact decimal, not a binary floating-point accounting value."
)
VERSION = integer(1)
PATH = string(
    minLength=1,
    maxLength=1024,
    description="Relative POSIX path, at most 1024 UTF-8 bytes. URL query/path uses exactly one transport percent-decoding; JSON/multipart path values and response paths are literal data, never percent-decoded. Reject absolute paths, empty/dot/dot-dot components, backslash, NUL, case-insensitive .git, symlinks and submodules in any traversed component; never follow links. Do not decode again in storage/adapter calls or normalize away forbidden components. Remaining percent sequences are literal filename characters, never a second traversal. Unsafe path/object gives422 INVALID_PATH; for reads, missing authorized path404. Authorization precedes object lookup.",
)
DIRECTORY_PATH = {
    **PATH,
    "minLength": 0,
    "default": "",
    "description": "Omitted or empty path selects repository root. Otherwise apply FilePath byte/path/object/traversal/single-decoding protections unchanged. "
    + PATH["description"]
    + " The selected object must be a directory, else422 INVALID_PATH.",
}


def build():
    doc = json.loads((ROOT / "prod-guild-openapi-v0.7-rest.json").read_text())
    original = copy.deepcopy(doc)
    doc["info"].update(
        title="PROD.Guild — candidate successor API (NOT FROZEN)",
        version="0.8.0-candidate.5",
        description="Кандидат, НЕ замороженный контракт и не runtime OpenAPI. Исторические 66 наблюдений Gitea 28.1.0 остаются спорными из-за scanner/classifier. Новый sole-lead-owner run prodguild-t01-lab-bf59da5559 прошёл66/66 существующих именованных наблюдений; отдельный local-matrix-correction.json содержит bounded local evidence с canaries-v2/exact-native-denial-v1. Независимое ревью ограничено исправлениями; reviewer не запускал live lab, полная isolation matrix не принята. Предлагаются приватный репозиторий на проект и PR внутри него, отдельные reference/draft-case репозитории, защищённые B/C refs. Принятие координатором ожидается; все component/full gates удержаны. Сохраняет60 операций/IDs v0.7; приложение сейчас реализует только существующие auth/health/metrics. GET не создаёт ресурсы. Локальный SQLite sandbox не означает одобрения production mirror, PostgreSQL или VM deployment.",
    )
    doc["x-contract-status"] = {
        "state": "candidate-not-frozen",
        "isolation_proven": False,
        "isolation_proven_scope": "complete-matrix-coordinator-accepted",
        "approved_release": None,
        "approved_image": None,
        "downstream_released": False,
        "gate_owner": "coordinator",
        "independent_review": "pending",
        "coordinator_acceptance": "pending",
        "component_gate": "held",
        "full_gate": "held",
        "decision": "architecture.md#gitea-identity-and-isolation",
        "local_evidence": {
            "state": "owner-run-reviewed-corrections-local-bounded-observations",
            "owner_authorized": True,
            "environment": "disposable-local-gitea-sqlite3",
            "release": "28.1.0",
            "image": "docker.gitea.com/gitea@sha256:f505a0d3a41323b786e05942fc7a4075e07ae7c169fd9485649aa55ee543a21a",
            "local_read_write_matrix_complete": True,
            "latest_fresh_complete": True,
            "historical_reported_rows": 66,
            "latest_corrected_evidence": "owner-run-existing-named-matrix-passed-66-of-66",
            "run_id": "prodguild-t01-lab-bf59da5559",
            "live_operator": "sole-lead-owner",
            "corrected_rows": {"total": 66, "passed": 66, "failed": 0},
            "corrected_policies": {
                "marker_scan": "prodguild-t01-actor-forbidden-body-canaries-v2",
                "native_denial": "prodguild-t01-gitea28.1.0-exact-native-denial-v1",
            },
            "independent_review": {
                "scope": "corrections-only",
                "full_matrix_accepted": False,
                "reviewer_operated_live": False,
            },
            "dispute": "scanner-and-classifier",
            "dispute_scope": "historical-artifact-only",
            "new_live_proof": True,
            "historical_evidence_path": "tests/integration/gitea-spike/evidence/local-matrix.json",
            "latest_corrected_evidence_path": "tests/integration/gitea-spike/evidence/local-matrix-correction.json",
            "evidence_scope": "actual-named-observations-only",
            "historical_observations": [
                "private-per-project-same-repo-pr-and-authorized-read-collaborator-links",
                "separate-private-reference-and-draft-case-repositories",
                "protected-base-and-head-api-git-mutation-denial-with-positive-write-controls",
                "retained-base-and-head-survive-branch-movement-gc-restart",
                "student-incidental-comments-allowed-canonical-projection-edit-delete-denied",
                "shared-upstream-private-fork-pr-peer-diff-leak-reproduced",
                "authenticated-browser-login-and-account-authorized-links",
                "file-blob-concurrency-lock-ref-403-and-stale-sha-422",
                "native-comment-position-acceptance-does-not-validate-pinned-diff",
            ],
        },
        "production_gates": {
            "mirror_approved": False,
            "postgresql_validated": False,
            "vm_deployment_approved": False,
        },
    }
    doc["servers"] = [
        {
            "url": "http://localhost:8080",
            "description": "Existing local backend origin; business routes are proposed, not implemented.",
        }
    ]
    doc["x-scope"] = {
        "mvp": [
            "publication",
            "individual projects",
            "qualified solution review",
            "expert quality checks",
            "per-skill development/readiness",
            "wallet/karma/priority",
            "text editor and binary materials",
            "manual/automatic review preference",
        ],
        "next": ["T15 rechecks", "T16 binary editor/native-write/SSO refinements"],
        "roadmap": ["T17 employer selection with consent, P2P, improved attempts, recommendations"],
    }
    policies = {
        "version": "prodguild-policy-1",
        "authorization": "Every business authorization is server-side and resource-scoped. Stored auth user/admin is unchanged; user gets participant capability, organizer is an explicit separately provisioned grant. Admin does not imply ownership, expertise, academic editing or arbitrary wallet access. Profile grade is display-only.",
        "matching": "For EVERY case skill: current confirmed or explicitly supervised non-expert grant, value >= required_value, grade >= max(case floor, next author snapshotted skill grade capped at expert). Case floor: review_minus below 50, review_plus at 50..74.999, expert at >=75. Whenever the floor or selected grade is expert (including author review_plus/expert), require independently confirmed expert rights and confirmed grant in that skill, never provisional expert. >=75 ALWAYS requires that relevant confirmed expert. Prefer closest eligible grade then higher. Exclude author, duplicate active subjects; quality assessor is a confirmed expert in ALL case skills and neither author nor original reviewer. No eligible actor means persistent waiting, never relaxed rights.",
        "development": "Only finalized approved score >=70: score * weight * (1 + 0.2 * E / 100), independently per skill. E is snapshotted validated reviewer skill value, or confirming expert value if supervised. Decimal arithmetic, uncapped source contributions, display min(100,sum(distinct effective case sources)), ROUND_HALF_UP to 2 places. Academic score unchanged, karma no multiplier. One effective participant/case/skill source; policy/case/revision/assessor/weight/E/before/after recorded.",
        "grades": "Per skill: performer default; review_minus value>=25 and 2 distinct successful cases; review_plus value>=50, previous probation passed and 3 distinct expert-confirmed good underlying reviews; expert value>=75, earlier gates and independent relevant expert readiness confirmation. Successful approved case and confirmed quality thresholds are >=70. No self-confirmation, no public grant endpoint.",
        "probation": "Each new non-expert reviewer grade requires 3 distinct underlying reviews confirmed by relevant experts at quality>=70. Snapshot supervision at claim; it survives concurrent graduation. needs_revision keeps pending content private and gives actionable feedback, not automatic negative karma. Revisions keep review/reward identity and do not count twice.",
        "assignment": "One active assigned job per reviewer (review or quality_check), 24-hour lease. Completed reviews awaiting quality or needs_revision do not consume active assignment capacity. Draft id/assignment_id with version=1 is created in the claim transaction, never GET. Completion endpoints are kind-specific: assignment reviews require kind=review, quality-assessments require kind=quality_check; mismatch409 ASSIGNMENT_KIND_MISMATCH. Recheck jobs are not allocated in MVP. Completion revalidates current grants, exact draft version and live lease under DB locks. Expired/cancelled assignments cannot complete; stale completion =>409 ASSIGNMENT_EXPIRED. Completed original review assignments may be revised only through their own review resource, not completed again. Revision publication checks current rights and current revision/status; it does not resurrect an expired assignment.",
        "revision": "One stable ReviewResult per primary assignment. Initial completion appends immutable revision 1; supervised -> pending_quality_check, else published. Relevant expert quality assignment pins revision_id. confirmed quality>=70 publishes exact revision; needs_revision makes status=needs_revision, leaves learner result/effects hidden and permits original reviewer to PUT same draft then POST revisions with expected_revision_id and expected_draft_version. New revision creates a NEW quality job pinned to it; old assessment cannot publish it. Revision save/finalize revalidates original reviewer/current grants and completed, not expired/cancelled original assignment. Stale revision/draft =>409. Expert peer solution reviews remain required, no invented grade above expert.",
        "finalization": "Publication, effective result, progression/grants/probation, wallet/karma, activity and projection outbox commit in ONE UoW. PostgreSQL is authoritative. No rewards, XP or author-visible result before mandatory confirmation. Pending probation drafts/revisions and quality feedback remain PostgreSQL-only; only committed published solution-review revisions may be projected by the backend service account after required confirmation. Restricted quality results never go to learner-readable Git, even after solution-review publication. Learners cannot edit/delete backend-authored canonical projections. Incidental native comments/reviews are untrusted annotations, never grades, grants, confirmations, rewards or any other business effects. Git failure leaves retryable projection, not reversed rewards.",
        "wallet": "Integer PRODиты: welcome+10 once/account, successful case+3 once/participant/case after final approved>=70, qualifying published review+5 and karma+1 once/underlying review. Meaningful valid negative-verdict feedback also qualifies. Mandatory quality>=70 first when supervised; no reward for score alone. Append-only source-keyed ledger; ordinary learning/review free. No arbitrary public balance/karma mutation or silent clawback of spent ordinary rewards.",
        "priority": "FIRST purchase only: owned waiting primary job and 5 available PRODиты. Active existing-request retries follow priority_deduplication before these purchase checks. One active reservation/job; available=settled-reserved>=0. Eligible claim settles; cancel before claim releases. Assigned requests cannot be purchased/cancelled; assignment expiry refunds settled charge once before requeue. Reservation/claim/cancel/expiry use deterministic locks and permanent source IDs. A racing boost either settles on the claim or fails/releases without ineffective charge. Among eligible jobs take at most 3 priority then 1 ordinary when both exist; FIFO created_at,id within class, ordinary aging after 24h prevents repeated overtaking (aged ordinary precedes non-aged jobs). No qualification bypass or human SLA.",
        "publication": "Create complete metadata as draft only. Separate private reference singleton; PATCH reference readiness with expected_version and expected_commit_sha. Any reference file change increments reference version and resets readiness. Publish requires ready reference at exact current commit/version and complete case. Public Git links target separate read-only published snapshot repos, NEVER organizer authoring/draft repos or hidden branches; before first project a content update produces a new published snapshot, not private edits in a participant-readable repo. Unpublishing revokes current grants where possible, not already legitimately downloaded bytes. First project atomically freezes materials, reference, requirements/weights and case version; publication visibility may still change, substantive content may not.",
        "git": "Locally tested candidate: private per-project repository with same-repository PR between protected retained base B and head C; separate private reference and unpublished case repos. Shared-upstream private-fork PRs are rejected: live testing reproduced peer-diff leakage. Backend owns working/frozen/submitted refs; participant native repository content access is read-only, without repo admin/owner rights. Retain protected branch refs refs/heads/prodguild/submissions/<id> at head C and refs/heads/prodguild/bases/<id> at base B plus stable repo/PR IDs. Both refs must survive branch movement, GC/restart and deny participant API+web+Git mutation/deletion. Local read/write matrix passed within actual named observations in tests/integration/gitea-spike/evidence/local-matrix.json, including positive write controls and GC/restart survival; independent/coordinator acceptance remains pending. Never merge learner PRs into starters. SHA strings alone are insufficient.",
        "git_links": "Separate internal API origin from external web/HTTPS clone. Credential-free server-origin-validated links only, no user-supplied upstream URL. Separate provisioned Gitea accounts; app bearer tokens are not Git login. SSH null until actually enabled/tested. Useful direct repo/PR links target the private project repository for account-authorized read collaborators. PR URL may be null when unavailable or the viewer account/ACL is unprovisioned or unavailable; API feedback and pinned file/diff access remain available to authorized app users. Incidental native annotations do not forbid PR links or require a service-only assessment repository: PostgreSQL alone determines grades/grants and the backend authors the canonical projection. Local authenticated-browser observations passed; independent/coordinator acceptance remains pending.",
        "comments": "Application MUST validate path and line/side against the PINNED base/head diff before any projection; Gitea acceptance is not validation. line>0 requires side old/new and a real coordinate on a changed file's pinned diff; line=null requires side=null (file-level) and a path present in the retained base/head tree. For line comments reject invalid/non-diff positions and unchanged paths with422 INVALID_REVIEW_POSITION; unsafe paths give422 INVALID_PATH. Local Gitea accepted out-of-range new_position=999 with an empty hunk, unchanged README line1, and old_position=new_position=0 with an empty hunk. None establishes a valid native line/file anchor. PostgreSQL path/line/side/body metadata is authoritative, never reconstructed from incidental Gitea annotations. File-level comments use the chosen safe general-comment fallback, only for a committed published revision after required confirmation; do not advertise native file-comment support. Pending drafts and restricted quality feedback remain PostgreSQL-only.",
        "files": "Text UTF-8 <=1MiB bytes/file, binary uploads <=10MiB bytes/file; reject oversized body with413. Snapshots <=200 files and <=2MiB decoded UTF-8 text in total, no blind binary decoding;413 SNAPSHOT_TOO_LARGE, paginated listing/download alternatives. Lists include file/dir and binary,size,blob SHA. GET content returns JSON ProjectFile for text, octet-stream attachment with nosniff for binary. No active HTML/SVG on trusted application origin. Multipart path optional on replacements: query path is authoritative; if repeated in multipart require exact equality, else422 PATH_MISMATCH. Writes use blob SHA and case/reference version where applicable; stale =>409, API edits stop at submission.",
        "recovery": "Persist intent, deterministic namespace/name and business operation marker before bounded Git I/O outside long DB locks. Reconcile ambiguous success by name/PR base-head/marker/review revision ID before retry; native Git writes have no assumed Idempotency-Key support. 201 submission only once retained commit+PR+DB resource exist. DB and Git have no distributed transaction. Durable worker reconciles unfinished writes/projections.",
        "project_uniqueness": "One Project per (participant_id, case_id), enforced by a permanent DB business key across new or expired request keys and concurrent requests. First creation requires a published case, freezes its content and copies only the exact starter commit, never the reference. First201+Location; repeat returns existing200 with the same project/repository identity and Location, without another repository or business effects. Reauthorize the existing owned resource on every retry; request-key expiry never permits a second project.",
        "submission_uniqueness": "One Submission per project_id in MVP, enforced by a permanent DB business key independently of Idempotency-Key retention. Persist the canonical submitted payload (expected_commit_sha and summary) for comparison. An unchanged repeat, including a new or expired request key, returns existing200 with the same Submission, retained B/C, PR identity and Location; never create another PR, submission or job. A changed payload under a retained key gives409 IDEMPOTENCY_CONFLICT; a changed submission for an already-submitted project under a new/expired key gives409 PROJECT_SUBMITTED. Before first creation, a moved expected commit gives409 COMMIT_CHANGED. Concurrent unfinished creation gives409 OPERATION_IN_PROGRESS and is reconciled by project business key. First201 only after protected retained refs, PR and DB resource exist; reauthorize on every retry.",
        "idempotency": "Required Idempotency-Key on business mutations, scope actor+method+path+key; canonical payload hash includes query/path, multipart metadata and content bytes. Same request returns original safe outcome, successful creates replay200 with same resource/Location; changed payload409 IDEMPOTENCY_CONFLICT; concurrent unfinished409 OPERATION_IN_PROGRESS. Cache no credentials/foreign private response; reauthorize on replay. Retain >=24h; business deduplication permanent. Empty queue204 is cached too: a NEW key is needed to try the queue again. DELETE204 remains204 on same-key replay.",
        "errors": "Business errors are code/message/request_id, safe bounded details only; no raw upstream tracebacks, addresses, tokens or existence leaks. Hidden/foreign resources404; known forbidden capability/self-assessment403. Preserve legacy auth detail envelopes and WWW-Authenticate Bearer, not the business envelope. Caddy must forward /api/v1 unchanged, not strip it; /metrics private loopback only.",
        "gitea_conflicts": "Future adapter must classify writes by operation and verified upstream error semantics, not status alone. Local Gitea returned403 for a concurrent lock-ref conflict and422 for a stale blob SHA; translate these recognized conflicts to business409 CONFLICT and409 STALE_BLOB respectively. Do not classify every upstream403 as ACL denial, every422 as caller validation, or every403 as concurrency. Genuine authorization failures remain distinct; unclassified/ambiguous failures require bounded reconciliation or sanitized dependency errors, without exposing raw upstream messages.",
        "pagination": "limit default20 range1..100, offset>=0; collections sort created_at DESC,id ASC unless catalogue/queue/file/diff/ledger ordering explicitly specified. File directories sort path ASC (UTF-8 bytewise, case-sensitive, no locale or directory-first grouping); pinned diffs use their documented path/coordinate order. Wallet ledger uses owner-local sequence DESC. Empty lists200 []; allocation POST204 has no body. Reads never allocate jobs, drafts, repos, XP or effects.",
        "deferred": "Recheck endpoints/schemas retained but disabled in MVP (501 FEATURE_DEFERRED), T15 must establish per-skill escalation/source corrections before enabling. Employer endpoint retained but disabled501 pending explicit consent policy and T17 approval. T16 native writes/SSO/binary editor refinements remain next-phase, not removed; binary materials and text editor are MVP.",
    }
    policies.update(
        review_response_capture="BLOCKER: review/draft/revision mutation response capture is NOT compatible with T03's hard1048576-byte serialized-response ceiling. SaveReviewDraftRequest permits200 comments of10000 characters each; a valid ReviewDraft exceeds2000000 serialized bytes even with ASCII comments, and nested ReviewResult/revision/workspace/feedback resources inherit the risk. Binding remains held pending coordinated bounded-response/capture design; current review schema bounds remain unchanged, not silently accepted as compatible. Preferred proposal: metadata-only mutation success <=65536 serialized UTF-8 JSON bytes (identity, exact version/revision, status and authorized content URI), identical first/replay body with explicit201-to200 mapping; persist full content and safe outcome atomically, then retrieve exact-version content through separately authorized GET with a coordinated <=262144-byte serialized page representation. The paginated GET/metadata shapes require coordinator/T03/T09 signoff and are not implemented or added as accepted endpoints here. No truncation, replay-only redaction, hash-only content recovery, fake collaborator or relaxed T03 ceiling. GET itself does not use mutation capture; its representation remains part of the unresolved content-retrieval design.",
        wallet_history="Only the current owner's wallet ledger history, including ALL transaction kinds (welcome, case_reward, review_reward, priority_reserve, priority_release, priority_settle, priority_refund). Order sequence DESC before limit/offset; sequence is a required positive integer, immutable and unique per wallet owner, never a global sequence or created_at/id ordering. Preserve existing limit default20 range1..100 and offset>=0 pagination; empty list200[]. Timestamps/IDs are metadata, not ordering keys; concurrent new ledger events may shift offset pages. Candidate sequence field is a mapper proposal pending T11/coordinator signoff, not an implemented transport mapping. Foreign rows are never included; role-only admin/organizer grants no wallet access.",
        file_mutation_response="Candidate proposal pending coordinator signoff, not an implemented mapper: successful text file POST/PUT returns metadata-only ProjectFile with content=null; retrieve contents through the separately authorized content GET. Binary mutations return BinaryFileMetadata. Bound the entire serialized success JSON envelope to <=65536 UTF-8 bytes (64KiB), not just source text or field lengths. Source upload limits remain1MiB text/10MiB binary; JSON escaping can expand source bytes beyond the T03 journal's1MiB serialized-response ceiling, so never echo source content in mutation responses. First201 and replay200 preserve the exact safe metadata body and canonical Location; PUT200 replay preserves its body. DELETE204 has no body. The64KiB cap applies to mutation responses, not content GET; GET still enforces source limits and current authorization. Enforce the bound before finalizing a successful outcome, not by truncating metadata or falling back to an uncached success.",
        directory_reads="Omitted or empty directory path selects root; nonempty uses exactly the FilePath byte/path/object/traversal/single-decoding protections. List immediate file/directory metadata, no contents, sorted path ASC (UTF-8 bytewise, case-sensitive, no locale or directory-first grouping) before limit/offset; never created_at DESC/id ASC. Unsafe path/object or non-directory selection422 INVALID_PATH; missing/hidden/foreign404. "
        + DIRECTORY_PATH["description"],
        content_reads="Required nonempty FilePath selects a file, never root or a directory; directory/unsafe path/object422 INVALID_PATH; missing/hidden/foreign404. "
        + PATH["description"],
        assigned_source_read="Only the assigned relevant expert of kind=quality_check with CURRENT confirmed expert grants in ALL case skills may read the exact assignment-pinned source. First authenticate and check ownership/current grants: foreign, hidden or revoked access404, without disclosing assignment state. Then kind=review409 ASSIGNMENT_KIND_MISMATCH; recheck source remains T15-disabled501. Require status=assigned and live lease: expired/cancelled or elapsed lease409 ASSIGNMENT_EXPIRED; completed409 ASSIGNMENT_NOT_ACTIVE. Finally require the current quality job/assignment and assignment.revision_id == review.current_revision.id with review.status=pending_quality_check, else409 STALE_REVISION. Serialize one consistent DB snapshot: revision and review.current_revision must be identical immutable objects with that exact ID, and submission.id == review.submission_id. Error responses contain no source/revision content or current revision IDs. Old, replaced, expired or completed assignments never expose a different pending revision. No self-assessment by solution author/original reviewer.",
        priority_history="Only the owner may GET a priority request in ANY state: reserved, settled, cancelled or refunded. The owner's list includes ALL these states and terminal history, ordered created_at DESC,id ASC with pagination. No waiting-job or available-funds requirement applies to reads. Foreign/hidden singleton404; no other owner's rows. Stored admin/organizer status gives no bypass.",
        priority_deduplication="Before purchase-only waiting/funds checks, reauthorize and look up an active reserved request by owned primary job business key under the same locks as claim/cancel. For a new or expired request key, equal canonical facts (submission_id, primary job_id, cost and policy_version) return existing200 with the same request/reservation IDs and Location; no duplicate hold, debit, request or effects. Changed facts while reserved give409 PRIORITY_ALREADY_ACTIVE; never replace the original facts. No request body is accepted; facts are server-derived, not client-selected job/cost. Concurrent unfinished creation409 OPERATION_IN_PROGRESS. A retained key with changed payload409 IDEMPOTENCY_CONFLICT. Once settled/cancelled/refunded, keep permanent attempt/source history; a retained key replays its original safe outcome, not a new purchase. A NEW or expired key after cancelled/refunded may create a distinct attempt201 with fresh request/reservation/source IDs only if that primary job is waiting and no active reservation exists; settled/assigned is not purchasable (409 JOB_ALREADY_ASSIGNED). Equal facts must not resurrect a terminal hold. First creation checks available funds and reserves5 once; read terminal history through GET/list.",
        readiness_read="Applicant may self-read their application and its assessments in every state, without expert rights. Otherwise only an independent CURRENT CONFIRMED expert in the application's SAME competency may read; require grade=expert, grant=confirmed and expert_confirmed=true at each read, not historical snapshots or value alone. No readiness assignment/assessor registry is required: qualifying experts are directly authorized. Other actors, unrelated/supervised/revoked experts and role-only organizers get404. Assessment GET uses this identical rule and checks assessment.application_id equals the path application_id, else404.",
        readiness_assess="Only an independent CURRENT CONFIRMED expert in the application's SAME competency may assess: grade=expert, grant=confirmed, expert_confirmed=true, current rights rechecked under transaction. No readiness assignment/assessor registry is required. Applicant POST403 SELF_ASSESSMENT even if expert; other unauthorized actors404. Require pending application and expected_application_status=pending; terminal/concurrent changed decision409 APPLICATION_ALREADY_DECIDED. Revalidate immutable application evidence and current eligibility; application alone grants nothing. Assessment GET uses readiness_read, including applicant self-read and current expert reauthorization.",
        readiness_deduplication="At most one pending application per (applicant_id, competency_id, requested_grade), independent of request-key lifetime. Canonical request facts are competency_id and requested_grade; actor supplies applicant_id, qualifying evidence is server-snapshotted only on first creation. A new or expired request key with equal facts returns existing200 with the same application/evidence and Location; newly accrued evidence does not replace that pending snapshot or create another request. Changed facts for an existing pending skill application409 APPLICATION_ALREADY_PENDING; client evidence fields/unknown fields422, never replacement. Concurrent unfinished creation409 OPERATION_IN_PROGRESS; retained key changed payload409 IDEMPOTENCY_CONFLICT. After a terminal approved/needs_development decision, history remains owner-readable; a genuinely new eligible attempt under a new/expired key gets a fresh application ID/evidence snapshot201, not resurrection or a duplicate pending request. An already-current confirmed expert cannot reapply for the same grade (409 APPLICATION_NOT_ELIGIBLE); prior needs_development or revoked rights require current qualifying evidence/gates again. A retained key replays its original safe outcome; terminal dedup never silently starts a new attempt.",
    )
    policies["git"] = policies["git"].replace(
        "Local read/write matrix passed within actual named observations in tests/integration/gitea-spike/evidence/local-matrix.json, including positive write controls and GC/restart survival; independent/coordinator acceptance remains pending.",
        "Historical66 observations in tests/integration/gitea-spike/evidence/local-matrix.json remain disputed (scanner/classifier). Separate corrected evidence tests/integration/gitea-spike/evidence/local-matrix-correction.json records sole-lead-owner run prodguild-t01-lab-bf59da5559 passing66/66 existing named observations with canaries-v2/exact-native-denial-v1. This is bounded local evidence; independent review covers corrections only, not full-matrix acceptance, and the reviewer did not operate the live lab. All gates held.",
    )
    policies["git_links"] = policies["git_links"].replace(
        "Local authenticated-browser observations passed; independent/coordinator acceptance remains pending.",
        "Fresh browser observations are included only within the existing named rows of sole-lead-owner corrected run prodguild-t01-lab-bf59da5559; historical evidence remains separately disputed. Corrections-only independent review is not full-matrix/coordinator acceptance.",
    )
    policies["readiness_deduplication"] += (
        " Applicant identity is derived from the authenticated actor, never a client applicant_id field. A different competency is a separate business key, not changed facts against another pending skill. Invalid requested grades/unknown evidence fields remain422 under the current expert-only request schema."
    )
    doc["x-business-decisions"] = policies
    doc["x-prodguild-integration-boundary"] = {
        "state": "recommendations-pending-coordinator-signoff",
        "implemented_adapter": False,
        "review_response_capture": {
            "state": "blocked-pending-coordinated-design",
            "binding_gate": "held",
            "compatible_with_t03": False,
            "mapper_implemented": False,
            "t03_serialized_response_max_bytes": 1048576,
            "existing_review_schema_bounds": "unchanged-200-comments-times-10000-characters",
            "affected_operation_ids": [
                "get_api_v1_submissions_submission_id_feedback",
                "get_api_v1_me_reviews",
                "get_review_workspace",
                "get_review_assignment_draft",
                "replace_review_assignment_draft",
                "create_review_result",
                "get_review_result",
                "create_recheck_review",
                "get_assigned_source_review",
                "create_review_revision",
                "get_review_revision",
            ],
            "preferred_proposal": "metadata-only-mutations-and-separately-authorized-versioned-content-get",
            "proposed_mutation_json_max_bytes": 65536,
            "proposed_read_page_json_max_bytes": 262144,
            "proposal_accepted": False,
        },
        "wallet_history": {
            "state": "mapper-proposal-pending-t11-coordinator-signoff",
            "mapper_implemented": False,
            "sequence_scope": "wallet-owner",
            "order": "sequence-desc",
        },
        "file_mutation_response": {
            "state": "proposal-pending-coordinator-signoff",
            "mapper_implemented": False,
            "text_content": None,
            "serialized_success_json_max_bytes": 65536,
            "source_text_max_bytes": 1048576,
            "t03_serialized_response_max_bytes": 1048576,
            "t03_followup_commit": "1055b0e",
            "t03_branch_tip_at_handoff": "a71cc68",
        },
        "live_evidence": {
            "historical_path": "tests/integration/gitea-spike/evidence/local-matrix.json",
            "latest_corrected_evidence_path": "tests/integration/gitea-spike/evidence/local-matrix-correction.json",
            "fresh_run": "owner-run-reviewed-corrections-local-bounded-observations",
            "run_id": "prodguild-t01-lab-bf59da5559",
            "latest_fresh_complete": True,
            "live_operator": "sole-lead-owner",
            "rows": {"total": 66, "passed": 66, "failed": 0},
            "scope": "actual-named-observations-only",
            "corrected_policies": doc["x-contract-status"]["local_evidence"]["corrected_policies"],
            "independent_review": doc["x-contract-status"]["local_evidence"]["independent_review"],
            "failed_attempts": [
                {
                    "path": "tests/integration/gitea-spike/evidence/local-matrix-correction-attempt-01.json",
                    "passed": 44,
                    "total": 66,
                },
                {
                    "path": "tests/integration/gitea-spike/evidence/local-matrix-correction-attempt-02.json",
                    "passed": 65,
                    "total": 66,
                },
            ],
            "historical_rows_revalidated_by_old_labels": False,
        },
        "idempotency_key": {
            "public_schema": "length-only-1..128",
            "proposed_for_t03_confirmation": "^[A-Za-z0-9._~-]{1,128}$",
            "public_adoption": "pending-coordinator-confirmation",
        },
        "recommendations": "tests/integration/gitea-spike/CONTRACT.md#integration-recommendations-pending-signoff",
    }
    # Executable behavior examples, NOT adapter/DB/live evidence.
    source_facts = {
        "owned": True,
        "current_expert": True,
        "kind": "quality_check",
        "status": "assigned",
        "lease_live": True,
        "current_assignment": True,
        "pinned_revision_id": "revision-1",
        "current_revision_id": "revision-1",
        "review_status": "pending_quality_check",
    }
    doc["x-contract-scenarios"] = {
        "scope": "contract-examples-only-not-runtime-proof",
        "source_review": [
            {
                "name": name,
                "facts": {**source_facts, **changes},
                "status": status,
                "code": code,
                "visible_revision_ids": [changes.get("pinned_revision_id", "revision-1")] if status == 200 else [],
            }
            for name, changes, status, code in (
                ("live-revision-1", {}, 200, None),
                (
                    "two-revisions-old-claim-cannot-leak-revision-2",
                    {"current_revision_id": "revision-2"},
                    409,
                    "STALE_REVISION",
                ),
                (
                    "two-revisions-new-claim-only-revision-2",
                    {"pinned_revision_id": "revision-2", "current_revision_id": "revision-2"},
                    200,
                    None,
                ),
                ("replaced-claim-same-revision", {"current_assignment": False}, 409, "STALE_REVISION"),
                ("expired-claim", {"lease_live": False}, 409, "ASSIGNMENT_EXPIRED"),
                ("cancelled-claim", {"status": "cancelled"}, 409, "ASSIGNMENT_EXPIRED"),
                ("completed-claim", {"status": "completed"}, 409, "ASSIGNMENT_NOT_ACTIVE"),
                ("foreign-stale-claim", {"owned": False, "current_revision_id": "revision-2"}, 404, "NOT_FOUND"),
                ("revoked-expert", {"current_expert": False}, 404, "NOT_FOUND"),
                ("solution-review-kind", {"kind": "review"}, 409, "ASSIGNMENT_KIND_MISMATCH"),
                ("deferred-recheck", {"kind": "recheck"}, 501, "FEATURE_DEFERRED"),
                ("already-published", {"review_status": "published"}, 409, "STALE_REVISION"),
            )
        ],
        "active_attempts": [
            {
                "name": f"{resource}-{key}-{case}",
                "resource": resource,
                "key": key,
                "state": active_state if case != "terminal-new" else terminal_state,
                "equal_facts": case != "changed",
                "eligible_new": True,
                "status": 200 if case == "equal" else 409 if case == "changed" else 201,
                "same_identity": case == "equal",
                "new_effects": case == "terminal-new",
                "replace_evidence": False,
            }
            for resource, active_state, terminal_state in (
                ("priority", "reserved", "refunded"),
                ("readiness", "pending", "needs_development"),
            )
            for key in ("new", "expired")
            for case in ("equal", "changed", "terminal-new")
        ],
    }
    doc["x-git-boundaries"] = {
        "topology": "private-per-project-same-repo-pr",
        "shared_upstream_private_fork_prs": "rejected-peer-diff-leak",
        "reference_and_draft_repositories": "separate-private",
        "authoritative_store": "PostgreSQL",
        "links": {
            "credential_free": True,
            "viewer_account_acl_required": True,
            "authorized_read_collaborator_pr_links": True,
            "nullable_pr_reasons": ["unavailable", "account-unprovisioned", "acl-unavailable"],
            "incidental_annotations_block_pr_links": False,
        },
        "projection": {
            "writer": "backend-service-account",
            "source": "committed-published-review-revision",
            "learner_can_edit_or_delete_canonical_projection": False,
            "pending_drafts": "PostgreSQL-only",
            "pending_quality_feedback": "PostgreSQL-only",
            "restricted_quality_results": "PostgreSQL-only",
            "comment_metadata_authority": "PostgreSQL",
        },
        "incidental_annotations": {"trust": "untrusted", "business_effects": False},
    }
    doc["x-gitea-compatibility"] = {
        "write_error_classification": "operation-and-verified-error-semantics-not-status-alone",
        "recognized_write_conflicts": [
            {"observation": "concurrent-lock-ref", "gitea_status": 403, "api_status": 409, "api_code": "CONFLICT"},
            {"observation": "stale-blob-sha", "gitea_status": 422, "api_status": 409, "api_code": "STALE_BLOB"},
        ],
        "application_pinned_diff_validation_required": True,
        "upstream_comment_acceptance_proves_valid_position": False,
        "observed_unvalidated_positions": [
            "new_position=999-empty-hunk",
            "unchanged-README-line1",
            "both-positions-zero-empty-hunk",
        ],
        "file_comment_projection": "safe-general-comment-with-authoritative-db-metadata",
    }
    doc["x-policy-values"] = {
        "expert_required_value": 75,
        "review_plus_required_value": 50,
        "grade_values": {"performer": 0, "review_minus": 25, "review_plus": 50, "expert": 75},
        "probation_successes": 3,
        "success_score": 70,
        "quality_score": 70,
        "max_active_assignments": 1,
        "assignment_lease_hours": 24,
        "priority_cost": 5,
        "priority_burst": 3,
        "ordinary_aging_hours": 24,
        "welcome_reward": 10,
        "case_reward": 3,
        "review_reward": 5,
        "review_karma": 1,
        "expertise_bonus_max": 0.2,
        "display_cap": 100,
    }
    doc["x-open-questions"] = [
        "LOCAL EVIDENCE: owner-authorized official Gitea 28.1.0 pinned digest tested in disposable SQLite sandbox; pending coordinator acceptance, not a frozen contract",
        "LOCAL MATRIX: sole-lead-owner corrected run prodguild-t01-lab-bf59da5559 passed66/66 existing named observations under canaries-v2/exact-native-denial-v1; scope is bounded local evidence only. Historical66 and failed corrections01/02 remain separate. Independent review covers corrections, not accepted full isolation; all component/full gates held",
        "PENDING ACCEPTANCE: independent review and coordinator acceptance of per-project same-repo PR topology, account-authorized links and successor contract before downstream release",
        "SEPARATE PRODUCTION GATES: approved mirror, Gitea PostgreSQL compatibility and VM deployment authorization remain outstanding",
    ]
    c = doc["components"]
    s = c["schemas"]
    c["securitySchemes"]["bearerAuth"]["description"] = (
        "Existing opaque, revocable application bearer token, never JWT or Gitea credentials. Roles/grants loaded server-side. No UI role switching."
    )

    # Replace misleading v0.7 schemas, preserving component names used by old operations.
    s["Health"] = obj({"ok": {"type": "boolean", "enum": [True]}}, example={"ok": True})
    s["Readiness"] = obj(
        {
            "status": enum("ready", "not_ready"),
            "checks": obj({"database": enum("up", "down"), "repository_storage": enum("up", "down")}),
        },
        description="200 iff both up, otherwise503. Each probe deadline<=2s, total<=3s; no writes, sanitized output only.",
    )
    s["Error"] = obj(
        {
            "code": string(minLength=1),
            "message": string(minLength=1, maxLength=1000),
            "request_id": string(minLength=1),
            "details": obj({}),
        },
        ["code", "message", "request_id"],
        description="Safe business envelope only. details may contain documented safe fields, never arbitrary upstream data.",
    )
    s["LegacyError"] = obj({"detail": string()}, description="Existing auth error envelope, unchanged.")
    s["LegacyValidationError"] = obj(
        {
            "detail": array(
                obj(
                    {"loc": array({"oneOf": [string(), {"type": "integer"}]}), "msg": string(), "type": string()},
                    additionalProperties=True,
                )
            )
        },
        description="Existing FastAPI422 envelope; framework may include input/ctx fields in each item. Not changed by this candidate.",
    )
    s["SignUpRequest"] = obj(
        {"email": string(format="email"), "password": string(minLength=8, maxLength=1024, format="password")},
        additionalProperties=True,
    )
    s["SignInRequest"] = obj(
        {"email": string(format="email"), "password": string(minLength=1, maxLength=1024, format="password")},
        additionalProperties=True,
    )
    s["TokenResponse"] = obj({"access_token": string(), "token_type": enum("bearer", default="bearer")})
    s["CurrentUserResponse"] = obj({"id": UUID, "role": enum("user", "admin")})
    s["Role"] = enum(
        "participant",
        "organizer",
        description="Product capability label only; does not replace stored auth user/admin or establish academic grants.",
    )
    s["CaseCompetency"] = obj(
        {
            "competency_id": UUID,
            "required_value": VALUE,
            "weight": number(minimum=0, exclusiveMinimum=True, maximum=0.25),
        },
        description="required_value selects reviewer qualification (>=75 relevant confirmed expert); weight is independent unnormalized XP multiplier.",
    )
    s["SkillSnapshot"] = obj(
        {
            "competency_id": UUID,
            "value": VALUE,
            "grade": ref("Grade"),
            "grant": enum("none", "supervised", "confirmed"),
            "expert_confirmed": BOOL,
            "evidence_ids": array(UUID),
            "snapshotted_at": TIME,
        },
        description="Per-skill historical evidence, not authorization after rights revocation.",
    )
    s["UserCompetency"] = obj(
        {
            "competency_id": UUID,
            "name": string(),
            "value": VALUE,
            "grade": ref("Grade"),
            "grant": enum("none", "supervised", "confirmed"),
            "expert_confirmed": BOOL,
            "review_allowed": BOOL,
            "evidence_ids": array(UUID),
            "updated_at": TIME,
        },
        description="Rights server-derived per skill; academic value alone never confirms an expert.",
    )
    for name in ("PublicCase", "AdminCase", "CreateCaseRequest", "UpdateCaseRequest"):
        props = s[name]["properties"]
        props["competencies"].update(
            minItems=1,
            maxItems=100,
            **{
                "x-unique-by": "competency_id",
                "description": "Server rejects duplicate competency_id even when weights/values differ (422 DUPLICATE_COMPETENCY); uniqueItems alone is not sufficient.",
            },
        )
        props["difficulty"] = {
            "allOf": [ref("Grade")],
            "description": "Derived display only: max required_value <50 => review_minus, <75 => review_plus, otherwise expert. If omitted on creation derive; supplied mismatch422. Not an authorization input, author per-skill floor may be higher.",
        }
        if name in ("PublicCase", "AdminCase"):
            props["frozen"] = BOOL
            s[name]["required"].append("frozen")
        if name == "CreateCaseRequest":
            props["status"] = enum("draft", default="draft")
            s[name]["description"] = policies["publication"]
        if name == "UpdateCaseRequest":
            s[name]["description"] = policies["publication"]
    for name in ("TextFile", "WriteFileRequest", "UpdateFileRequest"):
        if "content" in s[name]["properties"]:
            s[name]["properties"]["content"].update(
                maxLength=1048576,
                description="UTF-8 text; additionally enforce <=1MiB encoded bytes, not merely character count.",
            )
        if "path" in s[name]["properties"]:
            s[name]["properties"]["path"] = PATH
        s[name]["additionalProperties"] = False
    s["ProjectFile"] = obj(
        {
            "path": PATH,
            "type": enum("file", "dir"),
            "content": string(nullable=True),
            "sha": string(nullable=True),
            "commit_sha": SHA,
            "size": integer(),
            "binary": BOOL,
            "download_url": string(nullable=True),
        },
        description="List metadata contains content=null; text content GET includes bounded content. Directory has sha=null,size=0,binary=false,download_url=null. Binary content never decoded as UTF-8. "
        + policies["file_mutation_response"],
    )
    s["BinaryFileMetadata"] = obj(
        {
            "path": PATH,
            "type": enum("file", "dir"),
            "sha": string(nullable=True),
            "size": integer(),
            "binary": BOOL,
            "download_url": string(nullable=True),
        },
        description="Authorized API download path only; directory has null sha/download, zero size, binary=false.",
    )
    for name in ("FileUploadForm", "FileReplaceForm"):
        s[name]["additionalProperties"] = False
        s[name]["properties"]["path"] = PATH
        s[name]["properties"]["file"]["description"] = (
            "<=10MiB bytes, stream bounded; binary case materials MVP; binary editor refinements T16."
        )
    s["FileReplaceForm"]["required"].remove("path")
    s["FileReplaceForm"]["description"] = (
        "Query path authoritative; optional multipart path must equal it exactly, else422 PATH_MISMATCH."
    )
    s["RepositoryLinks"] = obj(
        {
            "repository_id": integer(1),
            "web_url": string(format="uri"),
            "clone_url": string(format="uri"),
            "ssh_url": string(nullable=True),
            "default_branch": string(minLength=1),
            "private": BOOL,
            "native_write_allowed": {
                "type": "boolean",
                "enum": [False],
                "description": "Repository content/ref writes; incidental PR annotations may be possible but have no business authority.",
            },
            "native_file_comments": {"type": "boolean", "enum": [False]},
        },
        description=policies["git_links"],
    )
    s["Project"]["properties"]["working_branch"] = string()
    s["Project"]["required"].append("working_branch")
    s["Project"]["description"] = (
        "Isolated per-participant repository initialized from frozen starter commit, not an assumed private fork. upstream_repository is read-only starter provenance; reference never copied. "
        + policies["project_uniqueness"]
    )
    s["Project"]["x-business-key"] = ["participant_id", "case_id"]
    s["CreateProjectRequest"]["description"] = policies["project_uniqueness"]
    s["SubmitProjectRequest"]["properties"]["expected_commit_sha"] = SHA
    s["SubmitProjectRequest"]["description"] = policies["submission_uniqueness"]
    submission = s["Submission"]
    submission["properties"].pop("author_grade")
    submission["required"].remove("author_grade")
    submission["properties"].update(
        {
            "author_skills": array(ref("SkillSnapshot"), minItems=1, **{"x-unique-by": "competency_id"}),
            "retained_ref": string(pattern="^refs/heads/prodguild/submissions/[0-9a-f-]+$"),
            "base_commit_sha": SHA,
            "commit_sha": SHA,
            "pull_request_id": integer(1),
            "pull_request_number": integer(1),
            "review_repository_id": integer(1),
            "pull_request_url": string(
                format="uri",
                nullable=True,
                description="Useful credential-free same-repository PR link for an account-authorized read collaborator. Null when unavailable or viewer account/ACL is unprovisioned or unavailable, not merely because incidental native annotations are possible. Canonical feedback/grades/grants remain PostgreSQL-authoritative; authorized API feedback and pinned file/diff access remain available.",
            ),
        }
    )
    submission["properties"]["competencies"].update(minItems=1, **{"x-unique-by": "competency_id"})
    submission["required"] += [
        "author_skills",
        "retained_ref",
        "base_commit_sha",
        "pull_request_id",
        "pull_request_number",
        "review_repository_id",
    ]
    submission["description"] = (
        "201 requires retained protected commit/base and private per-project same-repository PR plus DB record. review_repository_id is the project's repository ID. Snapshots immutable, every skill matched; no shared starter PR merge. URLs require viewer account ACL, IDs stable. Incidental annotations are untrusted and cannot change canonical feedback or business effects."
        + " "
        + policies["submission_uniqueness"]
    )
    submission["x-business-key"] = ["project_id"]
    s["SubmissionSnapshot"] = obj(
        {
            "submission_id": UUID,
            "commit_sha": SHA,
            "files": array(ref("ProjectFile"), maxItems=200),
            "text_bytes": integer(maximum=2097152),
        },
        description=policies["files"],
    )
    s["ReviewComment"] = obj(
        {
            "path": PATH,
            "line": integer(1, nullable=True),
            "side": enum("old", "new", nullable=True),
            "body": string(minLength=1, maxLength=10000),
        },
        description=policies["comments"],
        oneOf=[
            {"properties": {"line": {"enum": [None]}, "side": {"enum": [None]}}},
            {"properties": {"line": integer(1), "side": enum("old", "new")}},
        ],
        **{
            "x-semantic-validation": [
                "line=null iff side=null",
                "line comments require a changed file in the exact pinned diff",
                "file-level path must exist in the retained base/head tree",
                "line and side map to exact pinned diff",
                "upstream acceptance is not position validation",
            ]
        },
        example={"path": "src/main.py", "line": None, "side": None, "body": "Проверьте обработку ошибок в этом файле."},
    )
    feedback_fields = {
        "strengths": string(maxLength=6000),
        "problems": string(maxLength=6000),
        "next_steps": string(maxLength=6000),
    }
    s["DraftFeedback"] = obj(feedback_fields)
    s["FinalFeedback"] = obj(
        {key: {**value, "minLength": 1} for key, value in feedback_fields.items()},
        description="Each field must contain non-whitespace meaningful text. No invented minimum word-count; server rejects whitespace-only fields.",
    )
    s["ReviewDraft"] = obj(
        {
            "id": UUID,
            "assignment_id": UUID,
            "submission_id": UUID,
            "reviewer_id": UUID,
            "version": VERSION,
            "score": number(minimum=0, maximum=100, nullable=True),
            "verdict": enum("approved", "changes_requested", nullable=True),
            "feedback": ref("DraftFeedback"),
            "comments": array(ref("ReviewComment"), maxItems=200),
            "saved_at": TIME,
        },
        description="Created with every assignment transaction at version1. Solution draft score/verdict may be null. Drafts remain PostgreSQL-only until exact revision publication and required confirmation; never store pending probation content in Git. Quality assignment also has an empty draft; quality assessment resource accepts separate quality content and does not overload solution score/verdict.",
    )
    s["SaveReviewDraftRequest"] = obj(
        {
            "expected_version": VERSION,
            "score": number(minimum=0, maximum=100, nullable=True),
            "verdict": enum("approved", "changes_requested", nullable=True),
            "feedback": ref("DraftFeedback"),
            "comments": array(ref("ReviewComment"), maxItems=200),
        },
        description="Whole replacement, incomplete content valid. Only assigned original reviewer; version increments on every successful save, stale409.",
    )
    s["CompleteReviewRequest"] = obj(
        {"expected_draft_version": VERSION},
        description="Consumes exact saved draft, not new score/body. Final score/verdict/feedback/positions and current grants validated. approved iff score>=70; changes_requested iff score<70. Invalid final content422; expired/stale409.",
    )
    s["CreateRevisionRequest"] = obj(
        {"expected_revision_id": UUID, "expected_draft_version": VERSION}, description=policies["revision"]
    )
    s["ReviewRevision"] = obj(
        {
            "id": UUID,
            "review_id": UUID,
            "number": VERSION,
            "draft_version": VERSION,
            "score": VALUE,
            "verdict": ref("ReviewVerdict"),
            "feedback": ref("FinalFeedback"),
            "comments": array(ref("ReviewComment"), maxItems=200),
            "reviewer_skills": array(ref("SkillSnapshot"), minItems=1, **{"x-unique-by": "competency_id"}),
            "created_at": TIME,
        },
        description="Immutable content at exact draft version; approved iff score>=70. Assessor snapshots are per skill, not global grade.",
    )
    s["ReviewRevision"]["oneOf"] = [
        {"properties": {"score": number(minimum=70), "verdict": enum("approved")}},
        {"properties": {"score": number(maximum=70, exclusiveMaximum=True), "verdict": enum("changes_requested")}},
    ]
    s["ReviewResult"] = obj(
        {
            "id": UUID,
            "assignment_id": UUID,
            "submission_id": UUID,
            "reviewer_id": UUID,
            "current_revision": ref("ReviewRevision"),
            "status": enum("pending_quality_check", "needs_revision", "published", "superseded"),
            "probationary": BOOL,
            "supervised_competency_ids": array(UUID, uniqueItems=True),
            "published_at": string(format="date-time", nullable=True),
            "projection_status": enum("not_eligible", "pending", "published", "retryable_failure"),
            "gitea_review_id": integer(1, nullable=True),
            "policy_version": string(),
        },
        description=policies["finalization"],
    )
    s["ReviewWorkspace"]["description"] = (
        "Exact retained commit snapshot and existing assignment draft. Account-authorized PR URL may be null when unavailable or unprovisioned; never GET creation or HEAD reads."
    )
    s["AssignedReviewContent"]["properties"]["revision"] = ref("ReviewRevision")
    s["AssignedReviewContent"]["required"].append("revision")
    s["AssignedReviewContent"]["description"] = (
        policies["assigned_source_read"]
        + " PR URL may be null when unavailable or viewer account/ACL is unprovisioned or unavailable."
    )
    s["AssignedReviewContent"]["x-semantic-validation"] = [
        "live-current-quality-assignment-only",
        "revision == review.current_revision",
        "revision.id == assignment.revision_id",
        "submission.id == review.submission_id",
        "consistent-db-snapshot-no-other-revision-content",
    ]
    s["ReviewTask"] = obj(
        {
            "id": UUID,
            "kind": enum("review", "quality_check", "recheck"),
            "submission_id": UUID,
            "review_id": string(format="uuid", nullable=True),
            "revision_id": string(format="uuid", nullable=True),
            "recheck_id": string(format="uuid", nullable=True),
            "draft_id": UUID,
            "status": enum("assigned", "completed", "expired", "cancelled"),
            "assigned_at": TIME,
            "lease_expires_at": TIME,
            "supervised_competency_ids": array(UUID, uniqueItems=True),
            "reviewer_skills": array(ref("SkillSnapshot"), minItems=1, **{"x-unique-by": "competency_id"}),
        },
        description=policies["assignment"],
    )
    for name in ("ReviewQualityAssessmentRequest", "ReviewQualityAssessment"):
        props = s[name]["properties"]
        props.update(assignment_id=UUID, revision_id=UUID)
        s[name]["required"] += ["assignment_id", "revision_id"]
        s[name]["description"] = (
            "Separate review-quality score, NOT solution score. Claimed unexpired quality assignment pinned to exact current revision; all-skill confirmed expert, excluding original reviewer and solution author. confirmed requires quality score>=70; needs_revision requires actionable feedback and may have any score. Current rights rechecked. Stale/replayed assessment cannot confirm another revision; source dedup survives request keys. Quality content is submitted here, not via solution draft. Restricted quality feedback/results stay PostgreSQL-only and never enter learner-readable Git, including after solution-review publication."
        )
        s[name]["oneOf"] = [
            {"properties": {"decision": enum("confirmed"), "score": number(minimum=70)}},
            {"properties": {"decision": enum("needs_revision")}},
        ]
        if name == "ReviewQualityAssessment":
            props.pop("assessor_grade")
            s[name]["required"].remove("assessor_grade")
            props["assessor_skills"] = array(ref("SkillSnapshot"), minItems=1, **{"x-unique-by": "competency_id"})
            s[name]["required"].append("assessor_skills")
    s["Probation"]["properties"].update(
        competency_id=UUID,
        required_checks=integer(3, maximum=3),
        successful_review_ids=array(UUID, uniqueItems=True, maxItems=3),
    )
    s["Probation"]["required"] += ["competency_id", "successful_review_ids"]
    s["Probation"]["description"] = policies["probation"]
    s["SkillProbations"] = array(
        ref("Probation"), description="One independent probation per skill/grade epoch; no global probation authority."
    )
    for name in ("ProgressionResult",):
        for prop in ("grade_before", "grade_after"):
            s[name]["properties"].pop(prop)
            s[name]["required"].remove(prop)
        s[name]["properties"]["policy_version"] = string()
        s[name]["required"].append("policy_version")
        s[name]["description"] = policies["development"]
    changes = s["CompetencyChange"]
    changes["properties"].update(
        source_id=UUID,
        case_version=VERSION,
        score=VALUE,
        weight=number(minimum=0, exclusiveMinimum=True, maximum=0.25),
        assessor_id=UUID,
        assessor_value=VALUE,
        uncapped_contribution=DECIMAL,
        uncapped_before=DECIMAL,
        uncapped_after=DECIMAL,
        policy_version=string(),
        source_revision_id=UUID,
    )
    changes["required"] += [
        "source_id",
        "case_version",
        "score",
        "weight",
        "assessor_id",
        "assessor_value",
        "uncapped_contribution",
        "uncapped_before",
        "uncapped_after",
        "policy_version",
        "source_revision_id",
    ]
    changes["description"] = policies["development"]
    s["DevelopmentPolicy"] = obj(
        {
            "version": enum("prodguild-policy-1"),
            "state": enum("configured"),
            "formula": enum("score * weight * (1 + 0.2 * E / 100)"),
            "success_score": number(enum=[70]),
            "quality_score": number(enum=[70]),
            "probation_required_reviews": integer(3, maximum=3),
            "display_cap": integer(100, maximum=100),
            "rounding": enum("ROUND_HALF_UP_2"),
            "grade_gates": array(
                obj(
                    {
                        "grade": ref("Grade"),
                        "minimum_value": VALUE,
                        "successful_cases": integer(),
                        "confirmed_good_reviews": integer(),
                        "previous_probation_required": BOOL,
                        "independent_readiness_required": BOOL,
                    }
                ),
                minItems=4,
                maxItems=4,
            ),
            "matching": string(),
            "expertise_source": string(),
        },
        description=policies["grades"],
        example={
            "version": "prodguild-policy-1",
            "state": "configured",
            "formula": "score * weight * (1 + 0.2 * E / 100)",
            "success_score": 70,
            "quality_score": 70,
            "probation_required_reviews": 3,
            "display_cap": 100,
            "rounding": "ROUND_HALF_UP_2",
            "grade_gates": [
                {
                    "grade": "performer",
                    "minimum_value": 0,
                    "successful_cases": 0,
                    "confirmed_good_reviews": 0,
                    "previous_probation_required": False,
                    "independent_readiness_required": False,
                },
                {
                    "grade": "review_minus",
                    "minimum_value": 25,
                    "successful_cases": 2,
                    "confirmed_good_reviews": 0,
                    "previous_probation_required": False,
                    "independent_readiness_required": False,
                },
                {
                    "grade": "review_plus",
                    "minimum_value": 50,
                    "successful_cases": 2,
                    "confirmed_good_reviews": 3,
                    "previous_probation_required": True,
                    "independent_readiness_required": False,
                },
                {
                    "grade": "expert",
                    "minimum_value": 75,
                    "successful_cases": 2,
                    "confirmed_good_reviews": 3,
                    "previous_probation_required": True,
                    "independent_readiness_required": True,
                },
            ],
            "matching": policies["matching"],
            "expertise_source": "Reviewer validated per-skill snapshot, or confirming expert per-skill snapshot when supervised.",
        },
    )
    s["Wallet"] = obj(
        {"settled": integer(), "reserved": integer(), "available": integer(), "unit": enum("PRODиты")},
        description="Read-only integer amounts; available=settled-reserved>=0, never money.",
        example={"settled": 10, "reserved": 5, "available": 5, "unit": "PRODиты"},
    )
    s["WalletTransaction"] = obj(
        {
            "id": UUID,
            "sequence": integer(
                1,
                description="Immutable positive sequence unique per wallet owner; required proposed T11 transport mapping. Not a global sequence.",
            ),
            "source_id": string(minLength=1),
            "kind": enum(
                "welcome",
                "case_reward",
                "review_reward",
                "priority_reserve",
                "priority_release",
                "priority_settle",
                "priority_refund",
            ),
            "settled_delta": {"type": "integer"},
            "reserved_delta": {"type": "integer"},
            "priority_request_id": string(format="uuid", nullable=True),
            "assignment_id": string(format="uuid", nullable=True),
            "created_at": TIME,
        },
        description="Append-only source-keyed event. Holds do not debit settled until allocation; safe owner-only history, no public writes. "
        + policies["wallet_history"],
        **{"x-sequence-scope": "wallet-owner", "x-sequence-immutable": True, "x-sequence-unique-per-owner": True},
    )
    s["PriorityRequest"] = obj(
        {
            "id": UUID,
            "submission_id": UUID,
            "job_id": UUID,
            "reservation_id": UUID,
            "owner_id": UUID,
            "cost": integer(5, maximum=5),
            "status": enum("reserved", "settled", "cancelled", "refunded"),
            "assignment_id": string(format="uuid", nullable=True),
            "settlement_source_id": string(nullable=True),
            "refund_source_id": string(nullable=True),
            "created_at": TIME,
        },
        description=policies["priority_history"] + " " + policies["priority_deduplication"],
    )
    priority_example = {
        "id": "00000000-0000-4000-8000-000000000010",
        "submission_id": "00000000-0000-4000-8000-000000000011",
        "job_id": "00000000-0000-4000-8000-000000000012",
        "reservation_id": "00000000-0000-4000-8000-000000000013",
        "owner_id": "00000000-0000-4000-8000-000000000014",
        "cost": 5,
        "status": "reserved",
        "assignment_id": None,
        "settlement_source_id": None,
        "refund_source_id": None,
        "created_at": "2026-10-16T00:00:00Z",
    }
    s["PriorityRequest"]["example"] = priority_example
    s["CreateReviewerApplication"] = obj(
        {"competency_id": UUID, "requested_grade": enum("expert")},
        description=policies["readiness_deduplication"],
    )
    s["ReviewerApplication"] = obj(
        {
            "id": UUID,
            "applicant_id": UUID,
            "competency_id": UUID,
            "requested_grade": enum("expert"),
            "status": enum("pending", "approved", "needs_development"),
            "evidence_ids": array(UUID),
            "created_at": TIME,
        },
        description=policies["readiness_read"] + " " + policies["readiness_deduplication"],
    )
    s["ReviewerApplication"]["example"] = {
        "id": "00000000-0000-4000-8000-000000000020",
        "applicant_id": "00000000-0000-4000-8000-000000000021",
        "competency_id": "00000000-0000-4000-8000-000000000022",
        "requested_grade": "expert",
        "status": "pending",
        "evidence_ids": ["00000000-0000-4000-8000-000000000023"],
        "created_at": "2026-10-16T00:00:00Z",
        "latest_assessment": None,
    }
    s["ReadinessAssessmentRequest"] = obj(
        {
            "expected_application_status": enum("pending"),
            "decision": enum("approved", "needs_development"),
            "feedback": string(minLength=1, maxLength=20000),
        },
        description=policies["readiness_assess"],
    )
    s["ReadinessAssessment"] = obj(
        {
            "id": UUID,
            "application_id": UUID,
            "assessor_id": UUID,
            "assessor_skill": ref("SkillSnapshot"),
            "decision": enum("approved", "needs_development"),
            "feedback": string(minLength=1),
            "created_at": TIME,
        },
        description=policies["readiness_read"] + " " + policies["readiness_assess"],
    )
    profile = s["Profile"]
    profile["properties"].update(
        grade={
            "allOf": [ref("Grade")],
            "description": "Highest qualified skill grade, default performer, DISPLAY ONLY, never authorization.",
        },
        probation=ref("SkillProbations"),
        karma=integer(readOnly=True),
        prodits=integer(readOnly=True, description="Available wallet amount, not settled balance."),
        wallet=ref("Wallet"),
        capabilities=array(enum("participant", "organizer"), uniqueItems=True),
    )
    profile["required"] += ["wallet", "capabilities"]
    profile["description"] = (
        "Owner-only profile with per-skill grants/evidence; stored role user/admin is separate from capability labels. Overall grade is display-only. Real read-only wallet/karma values, no null placeholders."
    )
    reference = s["ReferenceProject"]
    reference["properties"].update(
        version=VERSION, ready=BOOL, ready_commit_sha=string(pattern="^[0-9a-f]{40}$", nullable=True)
    )
    reference["required"] += ["version", "ready", "ready_commit_sha"]
    reference["description"] = (
        policies["publication"] + " Reference content/URL never enters public models or participant repositories."
    )
    s["ReferenceReadinessRequest"] = obj(
        {"expected_version": VERSION, "expected_commit_sha": SHA, "ready": BOOL},
        description="Organizer owner only, refuses frozen case; stale commit/version409. Ready applies to exact current reference commit.",
    )
    s["Submission"]["properties"]["base_retained_ref"] = string(pattern="^refs/heads/prodguild/bases/[0-9a-f-]+$")
    s["Submission"]["required"].append("base_retained_ref")
    s["ReviewerApplication"]["properties"]["latest_assessment"] = {
        **copy.deepcopy(s["ReadinessAssessment"]),
        "nullable": True,
        "description": "Persisted expert decision/feedback, visible to applicant and authorized independent relevant assessor; null until assessed.",
    }
    s["ReviewerApplication"]["required"].append("latest_assessment")
    s["PinnedFilesPage"] = obj(
        {
            "submission_id": UUID,
            "commit_sha": SHA,
            "side": enum("base", "head"),
            "entries": array(ref("BinaryFileMetadata"), maxItems=100),
            "offset": integer(),
            "next_offset": integer(nullable=True),
        },
        description="Immutable tree listing, path ASC at server-selected retained base/head. limit1..100 default20; next_offset=null ends listing; no user commit or HEAD selector. Downloads target the corresponding submission-scoped content endpoint with side/path.",
    )
    s["PinnedDiffFile"] = obj(
        {
            "old_path": string(nullable=True),
            "new_path": string(nullable=True),
            "status": enum("added", "modified", "deleted", "renamed", "binary"),
            "binary": BOOL,
            "additions": integer(),
            "deletions": integer(),
            "comment_path": PATH,
        },
        description="comment_path is the pinned new path, or old path for deletion. Binary entries have no line diff. Renames expose both original and new paths.",
    )
    s["PinnedDiffPage"] = obj(
        {
            "submission_id": UUID,
            "base_commit_sha": SHA,
            "head_commit_sha": SHA,
            "entries": array(ref("PinnedDiffFile"), maxItems=100),
            "offset": integer(),
            "next_offset": integer(nullable=True),
        },
        description="Pinned base..head changed files, comment_path ASC; never moving PR branch state. Use diff/lines for paginated old/new coordinates; works even when pull_request_url is null.",
    )
    s["PinnedDiffLine"] = obj(
        {
            "kind": enum("context", "addition", "deletion"),
            "old_line": integer(1, nullable=True),
            "new_line": integer(1, nullable=True),
            "text": string(maxLength=65536),
            "text_truncated": BOOL,
        },
        description="Coordinates are absolute source line numbers at retained base/head. Addition old_line=null; deletion new_line=null; context both positive. Text <=64KiB UTF-8 bytes/line; oversized line text explicitly truncated, coordinates remain complete. Full bounded file available from pinned content endpoint.",
    )
    s["PinnedDiffLinesPage"] = obj(
        {
            "submission_id": UUID,
            "base_commit_sha": SHA,
            "head_commit_sha": SHA,
            "path": PATH,
            "lines": array(ref("PinnedDiffLine"), maxItems=100),
            "offset": integer(),
            "next_offset": integer(nullable=True),
        },
        description="Stable offset over pinned full unified-diff lines; hunk headers omitted, all source coordinates retained. limit<=100 and <=256KiB text bytes/page (may return fewer). Binary diff has empty lines; invalid/unchanged path404. No mandatory native PR access.",
    )
    s["ReviewWorkspace"]["properties"].update(
        snapshot={**copy.deepcopy(s["SubmissionSnapshot"]), "nullable": True},
        snapshot_state=enum("embedded", "paginated"),
        files_url=string(),
        diff_url=string(),
    )
    s["ReviewWorkspace"]["required"] += ["snapshot_state", "files_url", "diff_url"]
    s["ReviewWorkspace"]["description"] = (
        "200 with existing draft and retained submission. If <=200 files and <=2MiB text, snapshot_state=embedded with snapshot. Otherwise paginated with snapshot=null and canonical /submissions/{id}/files and /diff links; workspace itself does not fail for aggregate size. Immutable base/head file/content/diff endpoints remain usable without Gitea PR access; never substitute project HEAD."
    )
    for field in ("snapshot", "snapshot_state", "files_url", "diff_url"):
        s["AssignedReviewContent"]["properties"][field] = copy.deepcopy(s["ReviewWorkspace"]["properties"][field])
        if field not in s["AssignedReviewContent"]["required"]:
            s["AssignedReviewContent"]["required"].append(field)
    s["AssignedReviewContent"]["description"] += (
        " Same aggregate bound and 200 paginated snapshot=null/files_url/diff_url fallback as ReviewWorkspace, including expert source-review. Exact assignment revision and retained B/C only; never project HEAD."
    )
    s["ReviewDraft"]["example"] = {
        "id": "00000000-0000-4000-8000-000000000001",
        "assignment_id": "00000000-0000-4000-8000-000000000002",
        "submission_id": "00000000-0000-4000-8000-000000000003",
        "reviewer_id": "00000000-0000-4000-8000-000000000004",
        "version": 1,
        "score": None,
        "verdict": None,
        "feedback": {"strengths": "", "problems": "", "next_steps": ""},
        "comments": [],
        "saved_at": "2026-10-16T00:00:00Z",
    }

    # Safe error vocabulary is explicit and machine-readable, not a catch-all409.
    doc["x-error-codes"] = {
        "400": ["BAD_REQUEST"],
        "401": ["UNAUTHENTICATED"],
        "403": ["FORBIDDEN", "SELF_ASSESSMENT"],
        "404": ["NOT_FOUND"],
        "409": [
            "CONFLICT",
            "STALE_VERSION",
            "STALE_BLOB",
            "COMMIT_CHANGED",
            "CASE_FROZEN",
            "REFERENCE_NOT_READY",
            "PROJECT_SUBMITTED",
            "IDEMPOTENCY_CONFLICT",
            "OPERATION_IN_PROGRESS",
            "CAPACITY_LIMIT",
            "ASSIGNMENT_EXPIRED",
            "ASSIGNMENT_NOT_ACTIVE",
            "ASSIGNMENT_KIND_MISMATCH",
            "STALE_REVISION",
            "INSUFFICIENT_FUNDS",
            "PRIORITY_ALREADY_ACTIVE",
            "JOB_ALREADY_ASSIGNED",
            "PRIORITY_NOT_CANCELLABLE",
            "APPLICATION_ALREADY_DECIDED",
            "APPLICATION_ALREADY_PENDING",
            "APPLICATION_NOT_ELIGIBLE",
        ],
        "413": ["PAYLOAD_TOO_LARGE", "SNAPSHOT_TOO_LARGE"],
        "422": [
            "VALIDATION_ERROR",
            "DUPLICATE_COMPETENCY",
            "PATH_MISMATCH",
            "INVALID_PATH",
            "INVALID_REVIEW_POSITION",
            "INVALID_FINAL_REVIEW",
        ],
        "501": ["FEATURE_DEFERRED"],
        "503": ["DEPENDENCY_UNAVAILABLE"],
    }
    for name, value in c["responses"].items():
        media = value.get("content", {}).get("application/json", {})
        if "example" in media:
            media["example"]["request_id"] = "req-example"
        value.setdefault("headers", {})["X-Request-ID"] = {
            "schema": string(),
            "description": "Safe correlation ID, same as body request_id.",
        }
    c["responses"]["Unauthorized"]["headers"]["WWW-Authenticate"] = {"schema": enum("Bearer")}
    c["responses"]["PayloadTooLarge"] = response("Error", "413 bounded payload/snapshot exceeded")
    c["responses"]["FeatureDeferred"] = response("Error", "501 FEATURE_DEFERRED: operation retained, disabled in MVP")
    c["parameters"]["FilePath"]["schema"] = PATH
    c["parameters"]["FilePath"]["description"] = policies["content_reads"]
    c["parameters"]["DirectoryPath"] = {
        "name": "path",
        "in": "query",
        "required": False,
        "schema": DIRECTORY_PATH,
        "description": policies["directory_reads"],
    }
    c["parameters"]["IdempotencyKey"]["description"] = policies["idempotency"]

    # Audited against every v0.7 operation, including implied access in summaries.
    # Apply these AFTER common descriptions so global policy cannot erase access.
    access_rules = {
        "public-status": "Anonymous status read; sanitized output only, no business resource access.",
        "authenticated-policy": "Authenticated users may read the shared skill catalogue/configured development policy; this grants no academic rights.",
        "published-case-only": "Authenticated catalogue/material reads expose published cases only. Draft/archived cases are omitted from lists and return404 on item/file reads. No reference contents/URLs or private authoring branches, including through downloads.",
        "case-owner-organizer-only": "Only the case-owner organizer may read/change this case, its materials or private reference contents/URLs; collection reads contain only that organizer's own cases, including drafts. Other organizers/participants get404 for hidden resources; admin role alone grants no ownership bypass.",
        "organizer-create-case": "Only an explicitly granted organizer may create a case in their own collection; the server sets ownership to the caller. Stored admin role alone is insufficient.",
        "current-actor-only": "Only the current actor's own resources/state are returned or changed. Never include another user's private projects, submissions, reviews, assignments, drafts, history or profile.",
        "participant-create-project": "Create only for the current participant, with server-derived ownership; a new project requires a published case. Existing-project retries must reauthorize the same owned resource.",
        "project-owner-only": "Only the project owner may read working files/project state or submit/edit the project. Assigned reviewers use retained submission/assignment resources, never this mutable working tree. Foreign projects/files return404; file mutations after submission return409 PROJECT_SUBMITTED. Unchanged submission repeats retain the documented existing200 semantics.",
        "submission-owner-only": "Only the submission author may read this personal submission resource; foreign submissions return404. Assigned actors use their assignment and explicitly authorized pinned resources.",
        "pinned-submission-read": "Only the submission author, currently authorized assigned reviewer/quality expert, or explicitly authorized case-owner organizer may read retained B/C content. Other students/organizers get404; never read moving HEAD or reference repositories.",
        "published-feedback-read": "Only the submission author, authorized assigned actors, or explicitly authorized case-owner organizer may read published solution feedback. Pending reviews and restricted quality content are omitted; possession of a submission ID grants no access.",
        "published-progression-read": "Only the submission author or explicitly authorized case-owner organizer may read finalized effective progression; before finalization return404. No pending effects, arbitrary organizer access or public promotion writes.",
        "assigned-quality-expert": "Only the currently assigned relevant expert with an unexpired kind=quality_check assignment may assess that exact revision. Revalidate all-skill confirmed expert rights; exclude solution author and original reviewer. No role-only organizer access.",
        "private-quality-read": "Only the original reviewer or authorized assigned relevant expert may read quality assessments, including after solution-review publication. The solution author and organizer by role alone have no access; hidden resources return404.",
        "submission-author-recheck": "T15-disabled. Only the solution author with a published effective review may request recheck of that same retained commit; no self-selected reviewer or access to another author's submission.",
        "recheck-resource-read": "T15-disabled. Only the solution author, assigned rechecker, or explicitly authorized case-owner organizer may read the request; other actors get404.",
        "consented-organizer-only": "T17-disabled. Only an explicitly authorized organizer with applicable participant consent may use employer discovery; no role-only disclosure of personal data.",
        "claim-own-assignment": "Only claim work for the current reviewer through server-side all-skill matching; caller cannot select submission/reviewer IDs. Exclude own work and duplicate subjects; enforce capacity and live grants atomically.",
        "assigned-reviewer-only": "Only the assigned reviewer may read this assignment or read/replace its draft. Draft identity is assignment/draft ID, not provisional review ID; the solution author, other reviewers and organizers cannot read it. Hidden assignments/drafts return404.",
        "assigned-review-workspace": "Only the assigned reviewer of kind=review may read this workspace, retained submission and existing draft. Wrong kind409 ASSIGNMENT_KIND_MISMATCH; other actors404. No draft initialization or moving HEAD reads.",
        "assigned-review-completion": "Only the assigned reviewer of an active unexpired kind=review assignment may complete its exact saved draft, with current all-skill grants. Wrong kind409 ASSIGNMENT_KIND_MISMATCH; expired/cancelled409 ASSIGNMENT_EXPIRED; other actors404.",
        "review-resource-read": "Pending review content is readable only by its original reviewer and a current confirmed all-skill expert with a live current quality assignment pinned to that exact current revision. An old/stale/replaced/expired/completed assignment grants no pending-content access here (404); the assignment source endpoint gives its documented state409 only after ownership/current-rights checks. Published revisions may also be read by the solution author and explicitly authorized case-owner organizer; other actors404. Quality feedback remains separately restricted.",
        "assigned-recheck-reviewer": "T15-disabled. Only the assigned rechecker may create a recheck result for the original retained commit; require future accepted per-skill escalation rules, never organizer authority alone.",
        "assigned-source-review": policies["assigned_source_read"],
    }
    access_groups = {
        "public-status": ["get_healthcheck", "get_ready"],
        "authenticated-policy": ["list_competencies", "get_api_v1_development_policy"],
        "published-case-only": [
            "get_api_v1_cases",
            "get_api_v1_cases_case_id",
            "list_public_case_files",
            "get_public_case_file_content",
        ],
        "case-owner-organizer-only": [
            "get_api_v1_admin_cases",
            "get_api_v1_admin_cases_case_id",
            "patch_api_v1_admin_cases_case_id",
            "get_reference_solution",
            "put_reference_solution",
            "list_reference_solution_files",
            "create_reference_solution_file",
            "get_reference_solution_file_content",
            "replace_reference_solution_file_content",
            "delete_reference_solution_file_content",
            "list_admin_case_files",
            "create_admin_case_file",
            "get_admin_case_file_content",
            "replace_admin_case_file_content",
            "delete_admin_case_file_content",
        ],
        "organizer-create-case": ["create_admin_case"],
        "current-actor-only": [
            "get_api_v1_me_projects",
            "get_api_v1_me_submissions",
            "get_api_v1_me_reviews",
            "get_api_v1_me_probation",
            "get_api_v1_me_profile",
            "get_api_v1_me_history",
            "get_api_v1_me_recheck_requests",
            "get_api_v1_me_review_mode",
            "replace_review_mode",
            "list_my_review_assignments",
        ],
        "participant-create-project": ["create_my_project"],
        "project-owner-only": [
            "get_api_v1_projects_project_id",
            "create_submission",
            "list_project_files",
            "create_project_file",
            "get_project_file_content",
            "replace_project_file_content",
            "delete_project_file_content",
        ],
        "submission-owner-only": ["get_api_v1_submissions_submission_id"],
        "pinned-submission-read": ["get_api_v1_submissions_submission_id_snapshot"],
        "published-feedback-read": ["get_api_v1_submissions_submission_id_feedback"],
        "published-progression-read": ["get_api_v1_submissions_submission_id_progression"],
        "assigned-quality-expert": ["create_review_quality_assessment"],
        "private-quality-read": ["get_api_v1_reviews_review_id_quality_assessments"],
        "submission-author-recheck": ["post_api_v1_submissions_submission_id_recheck_requests"],
        "recheck-resource-read": ["get_api_v1_recheck_requests_recheck_id"],
        "consented-organizer-only": ["list_b2b_candidates"],
        "claim-own-assignment": ["create_review_assignment"],
        "assigned-reviewer-only": [
            "get_review_assignment",
            "get_review_assignment_draft",
            "replace_review_assignment_draft",
        ],
        "assigned-review-workspace": ["get_review_workspace"],
        "assigned-review-completion": ["create_review_result"],
        "review-resource-read": ["get_review_result"],
        "assigned-recheck-reviewer": ["create_recheck_review"],
        "assigned-source-review": ["get_assigned_source_review"],
    }
    access_by_id = {operation_id: rule for rule, ids in access_groups.items() for operation_id in ids}
    assert len(access_by_id) == sum(map(len, access_groups.values()))
    doc["x-operation-access"] = access_rules

    # Every original method/path/operationId survives; deferred operations stay visible.
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            deferred = "recheck" in path or path == "/api/v1/admin/candidates"
            phase = (
                "T17-roadmap-disabled"
                if path == "/api/v1/admin/candidates"
                else "T15-next-disabled"
                if deferred
                else "mvp"
            )
            op["x-delivery-phase"] = phase
            op["x-original-operation"] = True
            op["x-side-effects"] = method != "get"
            if "201" in op["responses"]:
                op["responses"]["201"].setdefault("headers", {}).setdefault(
                    "Location",
                    {
                        "schema": string(),
                        "description": "Canonical created resource URI. For files use the corresponding /files/content?path=<percent-encoded-path>; for reviews/quality/rechecks use returned stable resource ID.",
                    },
                )
            if method == "get":
                op["description"] = (
                    "Side-effect-free read; no allocation, repository/draft creation or business effects. "
                    + policies["authorization"]
                    + " "
                    + (
                        "limit default20 range1..100, offset>=0 for directory lists."
                        if "/files" in path
                        else policies["pagination"]
                    )
                )
                schema = (
                    op.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                if schema.get("type") == "array":
                    params = op.setdefault("parameters", [])
                    for name in ("Limit", "Offset"):
                        if {"$ref": f"#/components/parameters/{name}"} not in params:
                            params.append({"$ref": f"#/components/parameters/{name}"})
            elif path.startswith("/api/v1"):
                params = op.setdefault("parameters", [])
                key = {"$ref": "#/components/parameters/IdempotencyKey"}
                if key not in params:
                    params.append(key)
                op["description"] = policies["idempotency"] + " " + policies["authorization"]
                op["responses"].setdefault("409", {"$ref": "#/components/responses/Conflict"})
                op["responses"].setdefault("422", {"$ref": "#/components/responses/ValidationError"})
                if "201" in op["responses"]:
                    replay = copy.deepcopy(op["responses"]["201"])
                    replay["description"] = "Existing resource/same successful request replay, no duplicate effects."
                    op["responses"]["200"] = replay
            if deferred:
                op["deprecated"] = True
                op["description"] = policies["deferred"] + " " + op["description"]
                op["responses"]["501"] = {"$ref": "#/components/responses/FeatureDeferred"}
            if "/files" in path:
                op["description"] += " " + policies["files"] + " " + policies["gitea_conflicts"]
                op["responses"]["413"] = {"$ref": "#/components/responses/PayloadTooLarge"}
                if path.endswith("/files") and method == "get":
                    op["parameters"] = [
                        {"$ref": "#/components/parameters/DirectoryPath"} if p.get("name") == "path" else p
                        for p in op["parameters"]
                    ]
                    op["description"] += " " + policies["directory_reads"]
                    op["x-ordering"] = "path-asc-utf8-bytewise"
                    op["responses"]["200"]["description"] = policies["directory_reads"]
                if method == "get":
                    op["responses"]["422"] = {"$ref": "#/components/responses/ValidationError"}
                if method == "post":
                    op["description"] += " Create one new path; an already-existing path gives409 CONFLICT."
                if "/admin/cases/" in path and method != "get":
                    op["description"] += (
                        " Materials/reference edits after the first project give409 CASE_FROZEN. Reference edits increment reference version and reset readiness."
                    )
                if "/reference-solution/" in path and method != "get":
                    # Reference version is independent of blob SHA; each edit invalidates ready.
                    op.setdefault("parameters", []).append(
                        {"name": "expected_reference_version", "in": "query", "required": True, "schema": VERSION}
                    )
                if "/admin/cases/" in path and method != "get":
                    op.setdefault("parameters", []).append(
                        {"name": "expected_case_version", "in": "query", "required": True, "schema": VERSION}
                    )
            if "/files/content" in path and method == "get":
                op["description"] += " " + policies["content_reads"]
                op["responses"]["200"]["description"] = "Authorized bounded file content. " + policies["content_reads"]
                op["responses"]["200"]["content"] = {
                    "application/json": {"schema": ref("ProjectFile")},
                    "application/octet-stream": {"schema": string(format="binary")},
                }
                op["responses"]["200"]["headers"] = {
                    "Content-Disposition": {
                        "schema": string(),
                        "description": "attachment; sanitized filename for binary",
                    },
                    "X-Content-Type-Options": {"schema": enum("nosniff")},
                }
            if "/files" in path and method in {"post", "put", "delete"}:
                op["description"] += " " + policies["file_mutation_response"]
                op["x-prodguild-response-boundary"] = {
                    "state": "proposal-pending-coordinator-signoff",
                    "serialized_success_json_max_bytes": 65536,
                    "metadata_only": True,
                    "exact_first_replay_body": True,
                }
                for code in ("200", "201", "204"):
                    if code not in op["responses"]:
                        continue
                    result = op["responses"][code]
                    result["description"] += " " + policies["file_mutation_response"]
                    if code != "204":
                        media = result["content"]["application/json"]
                        if media["schema"] == ref("ProjectFile"):
                            media["schema"] = {
                                "allOf": [
                                    ref("ProjectFile"),
                                    {
                                        "type": "object",
                                        "properties": {"content": string(nullable=True, enum=[None])},
                                    },
                                ],
                            }

    def operation(path, method):
        return doc["paths"][path][method]

    def describe(path, method, text):
        operation(path, method)["description"] += " " + text

    def request(path, method, name):
        operation(path, method)["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref(name)}},
        }

    describe("/api/v1/admin/cases", "post", policies["publication"])
    describe("/api/v1/admin/cases/{case_id}", "patch", policies["publication"])
    operation("/api/v1/me/projects", "post")["summary"] = (
        "Создать изолированный проект участника (не общий upstream fork/PR)"
    )
    describe(
        "/api/v1/me/projects",
        "post",
        policies["project_uniqueness"] + " " + policies["git"],
    )
    describe(
        "/api/v1/projects/{project_id}/submissions",
        "post",
        policies["submission_uniqueness"] + " " + policies["git"] + " " + policies["recovery"],
    )
    for path, keys, policy in (
        ("/api/v1/me/projects", ["participant_id", "case_id"], "project_uniqueness"),
        ("/api/v1/projects/{project_id}/submissions", ["project_id"], "submission_uniqueness"),
    ):
        op = operation(path, "post")
        op["x-business-deduplication"] = {
            "unique_by": keys,
            "lifetime": "permanent",
            "new_or_expired_request_key": "return-unchanged-existing-200",
            "duplicate_git_resources": False,
            "policy": policy,
        }
        op["responses"]["200"]["description"] = policies[policy]
    describe(
        "/api/v1/submissions/{submission_id}/snapshot",
        "get",
        "Only owner, explicitly authorized assigned actors or case-owner organizer; never sibling students. Reads retained commit, no HEAD. "
        + policies["files"],
    )
    operation("/api/v1/submissions/{submission_id}/snapshot", "get")["responses"]["413"] = {
        "$ref": "#/components/responses/PayloadTooLarge"
    }
    describe(
        "/api/v1/submissions/{submission_id}/feedback",
        "get",
        "Author only sees published revisions/effective progression; pending review content and all restricted quality feedback/results omitted entirely in learner API and Git, including after solution-review publication. Native incidental annotations never become canonical feedback or business effects.",
    )
    describe("/api/v1/development/policy", "get", policies["development"] + " " + policies["grades"])
    operation("/api/v1/me/probation", "get")["responses"]["200"] = response("SkillProbations")
    describe("/api/v1/me/probation", "get", policies["probation"])
    describe(
        "/api/v1/me/probation",
        "get",
        "200 [] when no per-skill probation exists; never a global authoritative probation.",
    )
    describe(
        "/api/v1/competencies",
        "get",
        "Shared skill definitions; participant skill values are0..100, not Git solutions/references.",
    )
    describe(
        "/api/v1/admin/cases/{case_id}",
        "patch",
        "expected_version protects partial case changes; stale409 STALE_VERSION.",
    )
    describe(
        "/api/v1/me/review-assignments", "get", "Only current reviewer's active assignments, no hidden allocation."
    )
    describe(
        "/api/v1/me/reviews",
        "get",
        "Current reviewer's completed reviews, including their own pending-quality/needs-revision results.",
    )
    describe(
        "/api/v1/me/review-assignments",
        "post",
        policies["matching"]
        + " "
        + policies["assignment"]
        + " Manual claim works without enabled=true; automatic continuation only when enabled=true. No caller-selected submission_id."
        + " 204=no currently eligible job (queue empty or none qualified); X-Queue-State distinguishes empty/no_eligible. 503 is dependency failure,409 capacity. New key required to re-poll after cached204.",
    )
    operation("/api/v1/me/review-assignments", "post")["responses"]["204"] = {
        "description": "No eligible job, no body.",
        "headers": {"X-Queue-State": {"schema": enum("empty", "no_eligible")}},
    }
    describe(
        "/api/v1/review-assignments/{assignment_id}/draft",
        "get",
        "Existing draft only, created atomically on assignment; never initialize on GET.",
    )
    describe("/api/v1/review-assignments/{assignment_id}/draft", "put", policies["revision"])
    describe("/api/v1/review-assignments/{assignment_id}/draft", "put", s["SaveReviewDraftRequest"]["description"])
    for suffix in ("workspace", "source-review"):
        operation("/api/v1/review-assignments/{assignment_id}/" + suffix, "get")["responses"]["409"] = {
            "$ref": "#/components/responses/Conflict"
        }
    operation("/api/v1/review-assignments/{assignment_id}/source-review", "get")["responses"]["501"] = {
        "$ref": "#/components/responses/FeatureDeferred"
    }
    source_read = operation("/api/v1/review-assignments/{assignment_id}/source-review", "get")
    source_read["responses"]["404"] = {"$ref": "#/components/responses/NotFound"}
    source_read["responses"]["200"]["description"] = policies["assigned_source_read"]
    source_read["responses"]["409"] = response(
        "Error",
        "Owned/currently qualified only: ASSIGNMENT_KIND_MISMATCH, ASSIGNMENT_EXPIRED, ASSIGNMENT_NOT_ACTIVE or STALE_REVISION; no source content/IDs. "
        + policies["assigned_source_read"],
    )
    request("/api/v1/review-assignments/{assignment_id}/reviews", "post", "CompleteReviewRequest")
    describe(
        "/api/v1/review-assignments/{assignment_id}/reviews",
        "post",
        policies["matching"]
        + " "
        + policies["assignment"]
        + " "
        + policies["revision"]
        + " "
        + policies["finalization"],
    )
    describe(
        "/api/v1/reviews/{review_id}/quality-assessments",
        "post",
        policies["revision"] + " " + policies["probation"] + " " + policies["finalization"],
    )
    describe(
        "/api/v1/reviews/{review_id}/quality-assessments",
        "get",
        "Quality feedback/results only to original reviewer and assigned relevant expert, not solution author/organizer by role alone, including after solution-review publication. Restricted content remains PostgreSQL-only, never learner-readable Git.",
    )
    describe(
        "/api/v1/reviews/{review_id}",
        "get",
        "Pending content only to original reviewer and current confirmed all-skill expert with live current assignment pinned to current revision; old/stale/replaced/expired/completed assignment grants no access (404). Author only after published; foreign404.",
    )
    describe(
        "/api/v1/admin/cases/{case_id}/reference-solution",
        "put",
        "Separate private singleton initialized from case materials, never automatic ready; first201 existing200 on unchanged state even with new/expired request key, no second repository. No participant, Submission or PR belongs to the reference. Case owner organizer only, no frozen changes. "
        + policies["publication"],
    )
    operation("/api/v1/admin/cases/{case_id}/reference-solution", "put")["parameters"].append(
        {"name": "expected_case_version", "in": "query", "required": True, "schema": VERSION}
    )
    describe(
        "/api/v1/me/review-mode",
        "put",
        "Only changes saved enabled preference. Client may subsequently POST assignment with NEW request key if enabled, never server-side hidden claim on GET/PUT. Disable retains existing assignments. Ordinary reviews free.",
    )
    describe(
        "/api/v1/submissions/{submission_id}/recheck-requests",
        "post",
        "T15 retains one request per original primary review business key; unchanged retry returns existing200, never an unbounded recheck chain. Server selects the published effective review and same retained commit; no caller-selected assessor.",
    )
    describe(
        "/api/v1/recheck-requests/{recheck_id}/reviews",
        "post",
        "T15 must retain the prior result in history and replace effective_review_id for the same retained commit, correcting existing source contributions without duplicate effects before enabling this operation.",
    )
    operation("/healthcheck", "get")["description"] = (
        "Existing dependency-free process liveness: exactly {ok:true}, no DB/Gitea calls."
    )
    operation("/ready", "get")["description"] = s["Readiness"]["description"]

    def add(path, method, name, output, body=None, parameters=(), description="", created=False):
        op = {
            "operationId": name,
            "summary": name,
            "tags": ["Развитие"],
            "x-delivery-phase": "mvp",
            "x-original-operation": False,
            "x-side-effects": method != "get",
            "description": description + " " + policies["authorization"],
            "parameters": list(parameters),
            "responses": {
                "201" if created else "200": response(output, location="Canonical resource URI" if created else None),
                **{
                    str(code): {"$ref": f"#/components/responses/{res}"}
                    for code, res in [
                        (401, "Unauthorized"),
                        (403, "Forbidden"),
                        (404, "NotFound"),
                        (409, "Conflict"),
                        (422, "ValidationError"),
                        (503, "UpstreamUnavailable"),
                    ]
                },
            },
        }
        if method != "get":
            op["parameters"].append({"$ref": "#/components/parameters/IdempotencyKey"})
            op["description"] += " " + policies["idempotency"]
        else:
            op["description"] += " Side-effect-free. " + (
                "limit default20 range1..100, offset>=0; use the explicit path/coordinate order above."
                if "/files" in path or "/diff" in path
                else policies["pagination"]
            )
        if created:
            op["responses"]["200"] = response(
                output, "Successful request replay/existing resource", location="Same canonical resource URI"
            )
        doc["paths"].setdefault(path, {})[method] = op
        if body:
            request(path, method, body)
        return op

    def path_param(name):
        return {"name": name, "in": "path", "required": True, "schema": UUID}

    review_param = [{"$ref": "#/components/parameters/ReviewId"}]
    add(
        "/api/v1/reviews/{review_id}/revisions",
        "post",
        "create_review_revision",
        "ReviewRevision",
        "CreateRevisionRequest",
        review_param,
        policies["revision"],
        True,
    )
    revision_item = "/api/v1/reviews/{review_id}/revisions/{revision_id}"
    add(
        revision_item,
        "get",
        "get_review_revision",
        "ReviewRevision",
        parameters=review_param + [path_param("revision_id")],
        description="Immutable revision. Pending only to original reviewer or current confirmed all-skill expert with live current quality assignment pinned to this exact revision, which must still be current. Old/stale/replaced/expired/completed claims confer no access (404); solution owner sees only published revisions. Case-owner organizer needs explicit published-resource access, never an admin ownership bypass.",
    )
    quality_item = "/api/v1/reviews/{review_id}/quality-assessments/{assessment_id}"
    add(
        quality_item,
        "get",
        "get_quality_assessment",
        "ReviewQualityAssessment",
        parameters=review_param + [path_param("assessment_id")],
        description="Persisted exact revision assessment. Only original reviewer and authorized assigned relevant expert; not solution author or organizer by role alone, including after publication.",
    )
    sub_param = [{"$ref": "#/components/parameters/SubmissionId"}]
    page_param = [{"$ref": "#/components/parameters/Limit"}, {"$ref": "#/components/parameters/Offset"}]
    side_param = [{"name": "side", "in": "query", "schema": enum("base", "head", default="head")}]
    pinned_access = "Only submission owner, current authorized assigned reviewer/quality expert or explicitly authorized case-owner organizer; all foreign/hidden404. Exact retained B/C from submission, never caller commit, project HEAD or moving PR. Works with null native PR URL. "
    add(
        "/api/v1/submissions/{submission_id}/files",
        "get",
        "list_pinned_submission_files",
        "PinnedFilesPage",
        parameters=sub_param + page_param + side_param,
        description=pinned_access
        + "Immutable tree listing path ASC, not generic created_at ordering. List does not embed contents; follow pinned content URI using entry path and same side.",
    )
    pinned_content = add(
        "/api/v1/submissions/{submission_id}/files/content",
        "get",
        "get_pinned_submission_file",
        "ProjectFile",
        parameters=sub_param + side_param + [{"$ref": "#/components/parameters/FilePath"}],
        description=pinned_access
        + policies["files"]
        + "JSON commit_sha equals selected retained side. Binary download stays pinned. Unsupported oversized file413, directory422.",
    )
    pinned_content["responses"]["200"]["content"]["application/octet-stream"] = {"schema": string(format="binary")}
    pinned_content["responses"]["200"]["headers"] = {
        "Content-Disposition": {"schema": string(), "description": "attachment; sanitized filename for binary"},
        "X-Content-Type-Options": {"schema": enum("nosniff")},
        "Cache-Control": {"schema": enum("private, no-store")},
    }
    pinned_content["responses"]["413"] = {"$ref": "#/components/responses/PayloadTooLarge"}
    add(
        "/api/v1/submissions/{submission_id}/diff",
        "get",
        "list_pinned_submission_diff",
        "PinnedDiffPage",
        parameters=sub_param + page_param,
        description=pinned_access
        + "Changed files sorted comment_path ASC; base/head SHAs always returned. Paginate regardless of embedded snapshot limit.",
    )
    add(
        "/api/v1/submissions/{submission_id}/diff/lines",
        "get",
        "get_pinned_submission_diff_lines",
        "PinnedDiffLinesPage",
        parameters=sub_param + page_param + [{"$ref": "#/components/parameters/FilePath"}],
        description=pinned_access
        + "Paginate complete pinned diff coordinates with explicit line-text truncation, <=256KiB text/page and <=64KiB/line. Deleted files are readable on base side. Native position mapping must validate these exact old/new coordinates; out-of-diff comments422. No aggregate snapshot requirement.",
    )
    case_param = [{"$ref": "#/components/parameters/CaseId"}]
    add(
        "/api/v1/admin/cases/{case_id}/reference-solution",
        "patch",
        "set_reference_readiness",
        "ReferenceProject",
        "ReferenceReadinessRequest",
        case_param + [{"name": "expected_case_version", "in": "query", "required": True, "schema": VERSION}],
        policies["publication"],
    )
    add("/api/v1/me/wallet", "get", "get_my_wallet", "Wallet", description=policies["wallet"])
    wallet_history = add(
        "/api/v1/me/wallet/transactions",
        "get",
        "list_my_wallet_transactions",
        "WalletTransactions",
        parameters=[{"$ref": "#/components/parameters/Limit"}, {"$ref": "#/components/parameters/Offset"}],
        description=policies["wallet_history"],
    )
    wallet_history["description"] = policies["wallet_history"] + " " + policies["authorization"] + " Side-effect-free."
    wallet_history["x-ordering"] = "owner-sequence-desc"
    wallet_history["responses"]["200"]["description"] = policies["wallet_history"]
    s["WalletTransactions"] = array(ref("WalletTransaction"), description=policies["wallet_history"])
    ledger_example = []
    for sequence, (kind, settled_delta, reserved_delta, attempt, assigned) in enumerate(
        (
            ("welcome", 10, 0, None, False),
            ("priority_reserve", 0, 5, 1, False),
            ("priority_settle", -5, -5, 1, True),
            ("priority_refund", 5, 0, 1, True),
            ("priority_reserve", 0, 5, 2, False),
            ("priority_release", 0, -5, 2, False),
            ("case_reward", 3, 0, None, False),
            ("review_reward", 5, 0, None, False),
        ),
        start=1,
    ):
        ledger_example.append(
            {
                "id": f"00000000-0000-4000-8000-{100 + sequence:012d}",
                "sequence": sequence,
                "source_id": f"ledger-example-{sequence}",
                "kind": kind,
                "settled_delta": settled_delta,
                "reserved_delta": reserved_delta,
                "priority_request_id": f"00000000-0000-4000-8000-{200 + attempt:012d}" if attempt else None,
                "assignment_id": "00000000-0000-4000-8000-000000000301" if assigned else None,
                "created_at": "2026-10-16T00:00:00Z",
            }
        )
    s["WalletTransaction"]["example"] = ledger_example[0]
    wallet_history["responses"]["200"]["content"]["application/json"]["example"] = list(reversed(ledger_example))
    s["PriorityRequests"] = array(ref("PriorityRequest"))
    add(
        "/api/v1/submissions/{submission_id}/priority-requests",
        "post",
        "create_priority_request",
        "PriorityRequest",
        parameters=[{"$ref": "#/components/parameters/SubmissionId"}],
        description=policies["priority"] + " " + policies["priority_deduplication"],
        created=True,
    )
    add(
        "/api/v1/me/priority-requests",
        "get",
        "list_my_priority_requests",
        "PriorityRequests",
        parameters=[{"$ref": "#/components/parameters/Limit"}, {"$ref": "#/components/parameters/Offset"}],
        description=policies["priority_history"],
    )
    priority_param = [path_param("priority_request_id")]
    add(
        "/api/v1/priority-requests/{priority_request_id}",
        "get",
        "get_priority_request",
        "PriorityRequest",
        parameters=priority_param,
        description=policies["priority_history"],
    )
    cancel = add(
        "/api/v1/priority-requests/{priority_request_id}",
        "delete",
        "cancel_priority_request",
        "PriorityRequest",
        parameters=priority_param,
        description=policies["priority"],
    )
    cancel["responses"].pop("200")
    cancel["responses"]["204"] = {
        "description": "Cancelled before assignment, reservation released; internal history retained. Same-key replay204; already-cancelled with new key409 PRIORITY_NOT_CANCELLABLE."
    }
    add(
        "/api/v1/reviewer-applications",
        "post",
        "create_reviewer_application",
        "ReviewerApplication",
        "CreateReviewerApplication",
        description=policies["grades"] + " " + policies["readiness_deduplication"],
        created=True,
    )
    s["ReviewerApplications"] = array(ref("ReviewerApplication"))
    add(
        "/api/v1/me/reviewer-applications",
        "get",
        "list_my_reviewer_applications",
        "ReviewerApplications",
        parameters=[{"$ref": "#/components/parameters/Limit"}, {"$ref": "#/components/parameters/Offset"}],
        description="Only the applicant's own applications in ALL states, including terminal history. "
        + policies["readiness_read"],
    )
    app_param = [path_param("application_id")]
    add(
        "/api/v1/reviewer-applications/{application_id}",
        "get",
        "get_reviewer_application",
        "ReviewerApplication",
        parameters=app_param,
        description=policies["readiness_read"],
    )
    add(
        "/api/v1/reviewer-applications/{application_id}/assessments",
        "post",
        "create_readiness_assessment",
        "ReadinessAssessment",
        "ReadinessAssessmentRequest",
        app_param,
        policies["readiness_assess"],
        True,
    )
    readiness_item = "/api/v1/reviewer-applications/{application_id}/assessments/{assessment_id}"
    add(
        readiness_item,
        "get",
        "get_readiness_assessment",
        "ReadinessAssessment",
        parameters=app_param + [path_param("assessment_id")],
        description=policies["readiness_read"],
    )
    for path, keys, policy in (
        ("/api/v1/submissions/{submission_id}/priority-requests", ["job_id"], "priority_deduplication"),
        (
            "/api/v1/reviewer-applications",
            ["applicant_id", "competency_id", "requested_grade"],
            "readiness_deduplication",
        ),
    ):
        op = operation(path, "post")
        op["x-business-deduplication"] = {
            "unique_by": keys,
            "lifetime": "active-attempt-with-permanent-history",
            "new_or_expired_request_key": "equal-facts-existing-200-changed-facts-409",
            "duplicate_effects": False,
            "policy": policy,
        }
        op["responses"]["200"]["description"] = policies[policy]
    for path, policy in (
        ("/api/v1/me/priority-requests", "priority_history"),
        ("/api/v1/priority-requests/{priority_request_id}", "priority_history"),
        ("/api/v1/me/reviewer-applications", "readiness_read"),
        ("/api/v1/reviewer-applications/{application_id}", "readiness_read"),
        (readiness_item, "readiness_read"),
    ):
        operation(path, "get")["responses"]["200"]["description"] = policies[policy]
    operation("/api/v1/me/reviewer-applications", "get")["responses"]["200"]["description"] = (
        "Only the current applicant's own applications in ALL states; never list another applicant's rows, even for experts. "
        + policies["readiness_read"]
    )
    for code in ("200", "201"):
        operation("/api/v1/submissions/{submission_id}/priority-requests", "post")["responses"][code]["content"][
            "application/json"
        ]["example"] = copy.deepcopy(priority_example)
        operation("/api/v1/reviewer-applications", "post")["responses"][code]["content"]["application/json"][
            "example"
        ] = copy.deepcopy(s["ReviewerApplication"]["example"])
    priority_history_examples = {}
    for index, state in enumerate(("reserved", "settled", "cancelled", "refunded")):
        settled = state in {"settled", "refunded"}
        priority_history_examples[state] = {
            "value": {
                **priority_example,
                "id": f"00000000-0000-4000-8000-{30 + index:012d}",
                "reservation_id": f"00000000-0000-4000-8000-{40 + index:012d}",
                "submission_id": f"00000000-0000-4000-8000-{50 + index:012d}",
                "job_id": f"00000000-0000-4000-8000-{60 + index:012d}",
                "status": state,
                "assignment_id": f"00000000-0000-4000-8000-{70 + index:012d}" if settled else None,
                "settlement_source_id": f"priority-settle-example-{index}" if settled else None,
                "refund_source_id": f"priority-refund-example-{index}" if state == "refunded" else None,
            }
        }
    operation("/api/v1/priority-requests/{priority_request_id}", "get")["responses"]["200"]["content"][
        "application/json"
    ]["examples"] = priority_history_examples
    operation("/api/v1/me/priority-requests", "get")["responses"]["200"]["content"]["application/json"]["example"] = [
        e["value"] for e in priority_history_examples.values()
    ]
    # Each creation Location identifies a retrievable singleton, not a collection.
    for path, target in {
        "/api/v1/reviews/{review_id}/revisions": revision_item,
        "/api/v1/reviews/{review_id}/quality-assessments": quality_item,
        "/api/v1/reviewer-applications/{application_id}/assessments": readiness_item,
        "/api/v1/reviewer-applications": "/api/v1/reviewer-applications/{application_id}",
        "/api/v1/submissions/{submission_id}/priority-requests": "/api/v1/priority-requests/{priority_request_id}",
    }.items():
        for code in ("200", "201"):
            operation(path, "post")["responses"][code]["headers"]["Location"] = {
                "schema": string(),
                "description": "Canonical created resource URI: " + target,
                "example": target,
            }

    # Preserve the existing auth implementation, NOT v0.7's absent auth contract.
    for path, method, name, output, body, codes in [
        ("/auth/sign_up/email", "post", "auth_sign_up_email", "TokenResponse", "SignUpRequest", [409, 422]),
        ("/auth/sign_in/email", "post", "auth_sign_in_email", "TokenResponse", "SignInRequest", [401, 422]),
        ("/auth/me", "get", "auth_me", "CurrentUserResponse", None, [401]),
        ("/auth/sign_out", "post", "auth_sign_out", None, None, [401]),
    ]:
        op = {
            "operationId": name,
            "tags": ["Auth"],
            "summary": name,
            "x-delivery-phase": "existing",
            "x-original-operation": False,
            "x-side-effects": method != "get",
            "description": "Existing opaque-token auth; preserve exact status/envelope/challenge, signup always user; no request-key/role switching contract added.",
            "responses": {"200": response(output)} if output else {"204": {"description": "Signed out, empty body"}},
        }
        if path in ("/auth/sign_up/email", "/auth/sign_in/email"):
            op["security"] = []
        for code in codes:
            result = response("LegacyValidationError" if code == 422 else "LegacyError", "Existing legacy error")
            if code == 401:
                result["headers"] = {"WWW-Authenticate": {"schema": enum("Bearer")}}
            if code in (401, 409):
                result["content"]["application/json"]["example"] = {
                    "detail": "Authentication failed" if code == 401 else "User already exists"
                }
            op["responses"][str(code)] = result
        doc["paths"].setdefault(path, {})[method] = op
        if body:
            request(path, method, body)
    doc["paths"]["/metrics"] = {
        "get": {
            "operationId": "get_private_metrics",
            "summary": "Private metrics scrape",
            "description": "Existing loopback Sage-only text endpoint, never expose at public Caddy origin.",
            "x-delivery-phase": "existing-private",
            "x-original-operation": False,
            "x-side-effects": False,
            "security": [],
            "responses": {
                "200": {
                    "description": "Prometheus exposition (content type may include version parameter)",
                    "content": {"text/plain": {"schema": string()}},
                }
            },
        }
    }
    # OpenAPI3.0 nullable on an allOf/reference wrapper does not make its target
    # nullable. Inline these small object representations so real null responses
    # (before reference creation / before effective progression) validate too.
    for parent, field, target in (
        ("AdminCase", "reference", "ReferenceProject"),
        ("Feedback", "progression", "ProgressionResult"),
    ):
        s[parent]["properties"][field] = {**copy.deepcopy(s[target]), "nullable": True}
    # Sanity: retained original operations keep IDs, regardless of phase.
    original_ids = set()
    for path, item in original["paths"].items():
        for method in METHODS & item.keys():
            op = operation(path, method)
            operation_id = item[method]["operationId"]
            original_ids.add(operation_id)
            assert op["operationId"] == operation_id
            rule = access_by_id[operation_id]
            op["x-access-rule"] = rule
            op["description"] += "\n\nAccess: " + access_rules[rule]
    assert original_ids == set(access_by_id)
    readiness = operation("/api/v1/admin/cases/{case_id}/reference-solution", "patch")
    readiness["x-access-rule"] = "case-owner-organizer-only"
    readiness["description"] += "\n\nAccess: " + access_rules["case-owner-organizer-only"]
    review_gate = doc["x-prodguild-integration-boundary"]["review_response_capture"]
    for name in (
        "SaveReviewDraftRequest",
        "ReviewDraft",
        "ReviewRevision",
        "ReviewResult",
        "ReviewWorkspace",
        "AssignedReviewContent",
    ):
        s[name]["description"] += " " + policies["review_response_capture"]
        s[name]["x-prodguild-integration-gate"] = "review-response-capture-held"
    for item in doc["paths"].values():
        for method in METHODS & item.keys():
            op = item[method]
            if op["operationId"] not in review_gate["affected_operation_ids"]:
                continue
            op["x-prodguild-integration-gate"] = "review-response-capture-held"
            op["description"] += " " + policies["review_response_capture"]
    return doc


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(f"Generated {OUTPUT.name}: candidate-not-frozen (no security gate released)")
