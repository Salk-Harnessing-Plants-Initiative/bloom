## Context

bloom #11/#404's full request-driven pipeline trigger is `POST /workflows/pipeline` → enumerate →
dedup → insert tracking rows → chunk into batches → **submit one Argo workflow per batch** → poll for
status. Phase 1 (archived `add-cyl-pipeline-trigger`) built everything through "chunk into batches →
enqueue"; this proposal builds the "submit" step. The canonical architecture is
[sleap-roots-pipeline's 2026-07-06 A4 design doc](https://github.com/talmolab/sleap-roots-pipeline/blob/main/docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md)
(§3 end-to-end flow, §4 components, §6 execution topology); the cluster-side credential/RBAC/namespace
history lives in that same repo's `docs/bloom-integration/roadmap.md` and
`docs/superpowers/specs/2026-08-03-busch-lab-rbac-investigation-design.md`.

Four real, open questions had to be resolved before this could be designed at all — none of them are
answered by the canonical design doc, which is architecture-level and silent on K8s-API mechanics.
They were resolved this session (with the user, and by live testing against the cluster) rather than
assumed; see Decisions below for each.

## Goals / Non-Goals

**Goals (this phase):**
- Every batch Phase 1 enqueues onto `cyl_pipeline_dispatch` is actually submitted to Argo as a
  `Workflow` CRD, via the K8s API directly — not `argo submit`, not the in-cluster-only Argo Server.
- A durable record of what was submitted: each submitted batch's scan rows get their
  `argo_workflow_name` filled in; the run's `status` reflects submission outcome.
- Attribution labels on every submitted Workflow, since raw K8s API submission has no automatic
  `creator` label — a cluster-admin-stated hard requirement, not a nice-to-have.
- A submission failure is recorded, not silently lost, and doesn't crash the worker process.

**Non-goals (deferred, tracked for continuity, not implemented in this change):**
- Actually polling/reconciling a submitted Workflow's status (Phase 3 — `GET /workflows/runs/{id}`).
- Retrying a failed submission — terminal for now, matching PR #469's own explicitly-deferred retry
  design for the same reason (don't design retry semantics twice, independently, before either queue
  has real production experience).
- Per-request/per-lab namespace resolution — no data model exists to drive it; hardcoded for v1 (see
  Decision below).
- Closing the concurrent-duplicate-enqueue race Phase 1 flagged and left for this phase to decide "once
  real GPU cost is on the line" — it now is, and this proposal still doesn't close it (see Risks).

## Live validation (done during scoping, not merely designed on paper)

Before committing to the raw-REST-via-`httpx` approach, it was tested against the real cluster using
the already-provisioned `bloom-pipeline` ServiceAccount's kubeconfig for `runai-busch-lab`
(`~/.kube/kubeconfig-bloom-pipeline-busch-lab.yaml`, WSL). `kubectl auth whoami` confirmed the
identity (`system:serviceaccount:runai-busch-lab:bloom-pipeline`); `kubectl auth can-i` confirmed
`create`/`get`/`list`/`watch` on `workflows.argoproj.io` in `runai-busch-lab`, and confirmed `create` is
correctly **denied** in `runai-talmo-lab` (the credential is genuinely namespace-scoped, not just
labeled that way).

A minimal test `Workflow` (a single `busybox` step, no `hostPath`/GPU/real-data dependencies — cheap
and safe to run against the shared cluster) was POSTed via `kubectl create --raw
/apis/argoproj.io/v1alpha1/namespaces/runai-busch-lab/workflows -f <body>` — this exercises the exact
REST resource path and JSON body shape Phase 2's `httpx` call will use; `kubectl --raw` differs from
`httpx` only in which HTTP client library issues the request, not in the request itself. Result:
created successfully, labels (`submitted-by: bloom-pipeline`, `bloom-phase2-smoketest: "true"`,
`pipeline-run-id: smoketest-manual-1`) persisted on the object exactly as submitted, Argo's controller
picked it up within seconds, ran it, and it reached `Succeeded` with the expected stdout
(`bloom-phase2-k8s-rest-smoketest-ok`) — using `serviceAccountName: bloom-workflow` in the pod spec,
matching what the two real prior pipeline submissions visible in the namespace
(`sleap-roots-pipeline-mtbv5`/`-q62vv`) already use.

**One real constraint surfaced by this test, not anticipated beforehand**: cleaning up the test
Workflow with `kubectl delete` as `bloom-pipeline` failed —
`Forbidden: ... cannot delete resource "workflows"` — confirming the RBAC role genuinely grants no
`delete`. Cleanup required switching to a separate `argo-user` identity. This directly shapes a design
decision below (`ttlStrategy`, not code-driven cleanup).

**One inconsistency noted, not carried forward**: the existing real Workflows in the namespace carry a
label `project: talmo-lab` despite being submitted to `runai-busch-lab` — a stale label from an earlier
convention. This proposal's attribution labels do not copy it.

## Decisions

### Decision: a separate dispatch worker, not inline in `trigger_pipeline()`

The original framing of this work described extending `trigger_pipeline()` itself to submit
synchronously. Two independent signals point the other way: (1) `services/workflows/README.md` already
says "a later phase adds the worker that actually claims a batch and submits it to Argo," and
`pipeline.py`'s own docstring says (in different words) that submission is explicitly out of scope for
this phase — both written during Phase 1, before this proposal existed; (2) PR #469's
own design doc (`cyl-video-queue-phase2.md`) states outright that the pipeline-dispatch queue's future
consumer should model its claim/complete/fail shape on #469's, and Phase 1's `design.md` already
recorded the same pointer from that PR's review. Building a separate worker also means a transient
cluster/network blip doesn't fail the caller's `POST /workflows/pipeline` request — the batch just sits
enqueued and gets claimed once the worker is healthy again — whereas inline submission would make
every trigger request's latency (and success) hostage to the K8s API's availability, for a request that
today has zero such dependency.

- Alternatives considered: inline submission inside `trigger_pipeline()` — rejected; contradicts both
  signals above and couples request latency to cluster reachability. Confirmed with the user this
  session (see chat).

### Decision: raw REST via `httpx`, not the `kubernetes` PyPI client

`services/workflows` is deliberately minimal (7 dependencies) and runs hardened (`read_only` rootfs, no
volume mounts). `httpx` is already a dependency (used today for Supabase auth JWT validation). The
`kubernetes` PyPI client is a much heavier dependency (client-go-derived config loading, its own TLS/
auth stack) for what is, at the wire level, one JSON POST with a bearer token and a custom CA bundle —
exactly the shape `httpx` already handles for every other outbound call this service makes. Confirmed
with the user this session; live-validated (see above) that a raw REST POST to the literal
`argoproj.io/v1alpha1` resource path with the provisioned credential works end-to-end against the real
cluster, so there is no remaining unknown the heavier client would have de-risked.

- Alternatives considered: `kubernetes` PyPI client — rejected, new heavy dependency for no capability
  this service needs (no CRD watch, no client-side config merging, no multi-context handling).

### Decision: namespace is hardcoded to `runai-busch-lab` for v1

Neither `cyl_scans`, `cyl_experiments`, nor `cyl_waves` has any lab/project-ownership column today —
verified by direct grep across `supabase/migrations/` and every Python model in this repo. There is
therefore nothing to resolve a per-request namespace from. The user confirmed directly: "we are always
going to use busch lab." A config constant (`WORKFLOWS_K8S_NAMESPACE`, defaulting to `runai-busch-lab`,
overridable — not because v1 needs multiple namespaces, but because hardcoding a namespace as a literal
inside submission logic, with no override at all, would make even a future single-namespace change
require a code deploy instead of a config change) is used instead of a data-model change.

- Alternatives considered: (a) caller-specified namespace on the request body, validated against an
  allow-list — rejected for v1, pushes a decision onto every caller that the user says doesn't vary
  today; (b) block on adding a lab-ownership column first — rejected, real scope with no current
  driver; both remain available if/when `runai-talmo-lab` submissions are actually needed again.

### Decision: `ttlStrategy` for cleanup, not a delete call

Live validation (above) found `bloom-pipeline`'s RBAC has no `delete` on `workflows.argoproj.io` — this
was not something an earlier design could have assumed away, since neither the design doc nor the
roadmap document the least-privilege role's exact verb list at this granularity; it had to be checked
against the real cluster. Since the submitting identity can never delete what it creates, every
submitted Workflow carries `spec.ttlStrategy.secondsAfterCompletion` so Argo's own controller removes
completed Workflows after a bounded window, instead of leaving them to accumulate indefinitely in
`runai-busch-lab` (or requiring a second, more-privileged identity — `argo-user`, the one actually used
to clean up the live-validation test object — to exist in this service's runtime path, which would
undercut the least-privilege point of having `bloom-pipeline` at all).

- Alternatives considered: request a `delete` grant for `bloom-pipeline` — rejected without asking;
  widening the submission credential's RBAC to satisfy cleanup contradicts the least-privilege design
  the cluster admin already built, for a need `ttlStrategy` already solves natively.

### Decision: attribution labels are mandatory, not best-effort

Per 2026-08-06 cluster-admin feedback (recorded in the roadmap's "bloom trigger route" row): bypassing
Argo Server/the `argo` CLI means Workflows submitted this way never get Argo's automatic `creator`
label, and cluster-side attribution/filtering depends on *some* label existing. Every submission
therefore stamps `submitted-by: bloom-pipeline`, `pipeline-run-id: <run_id>`, `batch-index: <n>` before
the POST — not added opportunistically after success. Live-validated: labels supplied on the test
Workflow's `metadata.labels` persisted unchanged through creation and through the run to `Succeeded`.

### Decision: a fourth `environment` label, added on PR review

Found on review, after the namespace-hardcoded decision above was already made: prod and staging both
resolve to the identical `runai-busch-lab` namespace, and both `cyl_pipeline_runs.id` sequences start at
1 independently — so `pipeline-run-id: 42` alone can't tell a future reconciliation sweep (the one the
"successful-submission-recorded-as-failed" risk below already calls for) which database a given
Workflow's run actually belongs to. Resolution: a fourth label, `environment: <WORKFLOWS_K8S_ENV_LABEL>`
— a plain config value (default `"dev"`, never eagerly required) with the identical never-"missing"
treatment as `NAMESPACE`/`TTL_SECONDS` above, given a real, distinct entry in each of
`.env.prod.defaults` (`prod`) and `.env.staging.defaults` (`staging`).

### Decision: no direct `bloom_workflows` `UPDATE` grant — the wrapper functions write on its behalf

An earlier draft of this proposal added a column-scoped `UPDATE` grant, reasoning that Phase 1's
`design.md` had deferred it pending "a new endpoint that needs it." **This was reversed on review**:
`dispatch_worker.py` never issues a direct `UPDATE` against either table — every write happens inside
`claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch`, all three
`SECURITY DEFINER`, which run with their *owner's* privileges, not the caller's (`bloom_workflows`'s).
Phase 1's actual `INSERT` grant exists because `pipeline.py` calls the Supabase client's `.insert()`
directly against these tables — a real caller-privilege write. Phase 2 has no equivalent: the worker
only ever calls `.rpc(...)`. Granting `UPDATE` anyway would widen an already-shared role's privileges
for a capability nothing in this phase's code path actually uses — the opposite of the least-privilege
principle this same design doc applies everywhere else (see the `ttlStrategy` decision below).
Phase 1's original requirement text ("`bloom_workflows` SHALL NOT hold `UPDATE`") therefore stays true
as a matter of *behavior* — no grant is added. Its *wording* does get one small, text-only correction
via a `MODIFIED` delta on the same requirement (see `specs/cyl-pipeline-runs/spec.md`): Phase 1 phrased
the "no `UPDATE`" boundary as temporary ("a later phase... adds its own `UPDATE` grant"), and that
forecast is now simply wrong — this phase is that later phase, and its answer is "no grant, ever,"
not "not yet." The delta corrects the sentence to match; it does not change what the requirement
permits.

- Alternatives considered: grant `UPDATE` anyway, "for consistency with the `INSERT` grant" —
  rejected; the two grants exist for different reasons (direct-write vs. RPC-only), and copying one to
  match the other's shape isn't a reason to widen privilege nothing exercises.

### Decision: run-level status aggregation happens inside the wrapper functions, not in Python

`complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch` each update their own batch's scan rows, then
re-check `cyl_pipeline_run_scans` for that `run_id`: if every row now has either a non-null
`argo_workflow_name` or `status = 'failed'`, the run has no batches left to dispatch, so
`cyl_pipeline_runs.status` is set to `'submitted'` (all batches got a Workflow), `'partial'` (a mix),
or `'failed'` (none did) — all three already valid per Phase 1's existing `CHECK` constraint, no schema
change needed. Doing this aggregation inside the `SECURITY DEFINER` function (a single `UPDATE ...
WHERE NOT EXISTS (...)`-shaped statement) avoids a race between two workers completing the last two
batches of the same run near-simultaneously, which a read-then-write round trip from Python could lose.
`fail_cyl_pipeline_batch` also increments `attempts` on each of the batch's scan rows (mirroring PR
#469's `fail_cyl_video_job`), so a scan's row keeps a count of how many submission attempts it has been
part of even though this phase never retries automatically.

**`claim_cyl_pipeline_batch`'s own poison-message dead-letter path runs this exact same aggregation
check, not just `complete`/`fail`.** An earlier draft of this decision only described `complete`/`fail`
running it — a real gap, since a batch that gets dead-lettered by `claim` itself (redelivered past the
max-reads threshold, the worker having crashed repeatedly before ever calling `complete` or `fail`) also
marks its scan rows `'failed'`, and without the same run-completion check, a run whose *last* remaining
batch is dead-lettered this way would never transition off `'queued'`/`'submitted'` — stuck permanently,
with no code path left that will ever revisit it. All three functions (`claim`'s dead-letter branch,
`complete`, `fail`) therefore run the identical aggregation check.

- Alternatives considered: compute the aggregate in `dispatch_worker.py` after calling
  complete/fail — rejected, reintroduces exactly the race the DB-side approach avoids, and duplicates
  logic the DB can express atomically.

### Decision: per-scan `status` stays within the existing `CHECK` constraint — no `'submitted'` value added there

`cyl_pipeline_run_scans.status`'s `CHECK` constraint (`'queued'|'predicted'|'written'|'reused'|'failed'`)
has no `'submitted'` value, unlike `cyl_pipeline_runs.status` (which does). This is not a gap to patch:
a scan row's `status` describes the *pipeline's* outcome for that scan (predicted/written/reused by the
cluster-side stages, or failed), which this phase does not produce — it only produces the
*submission* outcome, recorded via `argo_workflow_name` (non-null = submitted) or `status = 'failed'`
(submission itself failed, distinct from a pipeline-stage failure, but sharing the same terminal value
since both mean "this scan got no useful pipeline outcome"). Adding a `'submitted'` per-scan status
would blur that distinction for no query anything in this phase needs — `argo_workflow_name IS NOT
NULL` already answers "was this scan's batch submitted."

### Decision: `WORKFLOWS_K8S_TTL_SECONDS` defaults to `3600` and is a plain config value, not a credential

Of the five `WORKFLOWS_K8S_*` variables, exactly **three** are real credentials, eagerly validated as
required with no safe default (`TOKEN`/`CA_CERT`/`API_URL` — a missing one raises `K8sConfigError`
before any network call). The other **two** — `NAMESPACE` and `TTL_SECONDS` — are plain config values
with a safe default (`runai-busch-lab`, `3600`) and are never validated as "missing"; `TTL_SECONDS`
gets the identical treatment `NAMESPACE` already has. Because neither is a secret, both must be sourced
the same way any other non-secret compose var is — a real entry in `.env.prod.defaults`/
`.env.staging.defaults` — **not** via the `SENSITIVE_INVENTORY` path the three real credentials use.
An implementer who puts all five (or even just `NAMESPACE`) into `SENSITIVE_INVENTORY` and skips a
plain default-file entry will fail `test_all_compose_vars_are_sourced`, or worse: `docker-compose
--env-file` would inject an empty string for the unentered var rather than leaving it unset, so
`os.environ.get(..., "runai-busch-lab")`-style code-level defaulting never actually triggers, and
submissions silently target an empty namespace segment instead of the intended one. Both `NAMESPACE`
and `TTL_SECONDS` need their own real default-file entries (see tasks.md §5.2).

### Decision: `WORKFLOWS_K8S_CA_CERT`'s PEM content is stored escaped, not raw, in the deploy pipeline

Found on review, distinct from (and in addition to) the `httpx`-side `verify=` conversion already
noted below: this repo's secret-injection pipeline (`.github/workflows/deploy.yml`'s heredoc →
`.env.prod`/`.env.staging` → `scripts/validate_env.sh` → docker-compose `--env-file`) assumes one
`KEY=VALUE` per physical line (`scripts/verify_env_parity.py`'s `LINE_PARSER` regex is anchored to a
single line). A real PEM certificate contains embedded newlines, which would break every one of those
line-oriented tools if stored raw. **Resolution**: the GitHub Secret backing `WORKFLOWS_K8S_CA_CERT`
stores the PEM with literal `\n` escape sequences instead of real newlines (a single physical line,
each newline written as the two characters `\`+`n`); `k8s_client.py` un-escapes (`.replace("\\n",
"\n")`) before constructing the `ssl.SSLContext`. This keeps every existing line-oriented deploy tool
working unmodified, at the cost of one small, well-contained unescape step in application code — the
same tradeoff this repo already accepts for any other multi-line secret, of which this is the first.

- Alternatives considered: base64-encode the whole PEM as the secret value, decode in `k8s_client.py`
  — equally valid, marginally more opaque to a human inspecting the secret; either is acceptable, this
  one was picked only because "escaped newlines" reads more directly as "this is PEM" to a future
  operator than an opaque base64 blob would.

### Decision: `K8sSubmissionError`'s message is sanitized before it reaches `error_message`

Found on review: `error_message` is a column this phase writes and a future phase (Phase 3, `GET
/workflows/runs/{id}`) will expose through an API — an unsanitized exception message could leak the
real K8s API server URL, response body, or other internal detail into a user-facing field. Mirroring
PR #469's `worker.py`'s own `_safe_detail` convention (a curated message for an expected error class,
a generic fallback for anything else — never the raw exception text verbatim): `k8s_client.py`'s
`submit_workflow` SHALL raise `K8sSubmissionError` with a fixed, generic message (e.g. "Argo Workflow
submission failed") for any non-2xx/network failure, and SHALL log the real status code/response body/
underlying exception at `logger.warning`/`logger.error` server-side only, never folding raw response
content into the exception's own message. `fail_cyl_pipeline_batch` then stores exactly that generic
message, never the raw network-layer detail.

### Decision: the worker finishes an in-flight claim before exiting on `SIGTERM`/`SIGINT`

Asserted as intended behavior in `proposal.md` (mirroring PR #469's graceful-shutdown handling) but,
found on review, not yet backed by a spec requirement or test. Promoted to an explicit decision: a
`SIGTERM`/`SIGINT` received while a batch is mid-submission SHALL let that submission (and its
`complete`/`fail` call) finish before the loop exits, rather than killing the process mid-POST — a
killed-mid-POST worker can't know whether the K8s API actually received the submission before the
signal landed, which is exactly the "successful-submission-recorded-as-failed" risk (see Risks) in
miniature. Unlike PR #469's video worker (whose `stop_grace_period` needs ~150s for a real encode to
finish), this worker's unit of work is a single HTTP POST, so a short grace period (on the order of the
`httpx` request timeout, not minutes) is sufficient — set to 30s in both compose files (`k8s_client.py`'s
`httpx` timeout is 15s). Found on PR review: that 30s budget only actually holds if the
`complete`/`fail` Supabase RPC that follows the K8s POST is itself bounded — supabase-py's un-overridden
default (`postgrest_client_timeout`) is 120s, which alone would blow the whole grace period. Fixed by
giving this service's own `supabase_client.py` an explicit `POSTGREST_TIMEOUT_SECONDS = 10` (unlike
`bloommcp`'s own client, which deliberately keeps the 120s default for its unrelated large-experiment-
fetch use case — see that module's own comment — this worker's RPCs are all single-batch, small,
indexed operations with no comparable payload to protect), keeping the worst case comfortably under
30s: 15s (POST) + 10s (RPC) = 25s.

### Decision: the worker retries its startup Supabase connection with backoff — a deliberate departure from PR #469

**Found during live boot validation against the real stack, not during code review.** PR #469's
`worker.py` calls `app_client()` once, unguarded, before entering its poll loop — if Supabase is
unreachable at that moment, the process raises an uncaught exception and dies. Reproduced exactly:
this worktree's local dev credentials weren't configured, and `dispatch_worker.py` (deliberately
matching #469's shape) crashed identically. Once the credentials were fixed, the deeper issue
remained: **any** transient Supabase outage at container startup — a deploy race, a brief DB blip —
would still crash the process, and Docker's `restart: unless-stopped` would crash-loop it forever.
The *in-loop* reconnect a few lines below already retries after a transient failure; the one-shot
startup connection was the only place that didn't.

**Resolution**: `run()` calls a new `_connect_with_retry()` instead of `app_client()` directly — it
retries with the same `POLL_INTERVAL` backoff the rest of the worker already uses, until either it
connects or a shutdown signal arrives (checked between attempts, so a signal during a long outage
still exits promptly rather than retrying forever). Verified against the real stack, not just mocked
tests: a one-off container instance given a deliberately wrong `WORKFLOWS_SUPABASE_PASSWORD` logged a
clean retry message every 5 seconds indefinitely and stayed `Up` — it never crash-looped.

This is a deliberate, narrow departure from the "model directly on PR #469" precedent this proposal
otherwise follows throughout — #469's video worker has the identical gap, unfixed here since it's out
of this proposal's scope, but worth noting for whoever next touches that file.

- Alternatives considered: leave it matching #469 exactly, file the robustness gap as a follow-up
  issue instead of fixing it now — rejected once actually observed crash-looping against the real
  stack; a worker that reliably crash-loops on a transient dependency outage is a worse regression to
  ship deliberately than a one-function, well-tested deviation from the reference pattern.

## Risks / Trade-offs

- **The concurrent-duplicate-enqueue race is now genuinely costly, and this proposal does not close
  it.** Phase 1's `design.md` documented that two overlapping `POST /workflows/pipeline` requests for
  overlapping scans each get their own `run_id` and both enqueue, calling this "bounded and acceptable
  in Phase 1" because nothing reached Argo yet. That is no longer true once this phase ships: two
  overlapping requests now mean two separately-submitted Argo Workflows processing the same scans —
  real duplicate GPU cost, not just duplicate DB rows. Phase 1 explicitly punted the real fix to
  whichever phase first has GPU cost on the line, which is this one. **This proposal still does not fix
  it** — closing it properly (a partial unique index on `(scan_id, param_hash)` scoped to non-terminal
  statuses, per Phase 1's own suggested candidate, generalizing PR #469's
  `cyl_video_jobs_one_active_per_scan` precedent) is a change to Phase 1's enumeration/insert path, not
  this phase's claim/submit/complete path, and reopening that path here would blur this proposal's
  scope. **Flagged explicitly for the approval decision**: ship this phase with the race still open (an
  explicit, informed re-deferral), or treat closing it as a prerequisite/companion change before this
  lands. Recorded here rather than silently re-deferred a second time.
- **`bloom_workflows` is shared across three call sites** (the on-demand/queued video paths, and now
  this dispatch worker) — an already-accepted tradeoff from PR #470. This proposal does not widen its
  grants at all (see the `UPDATE`-grant decision above); the worker calls only the three new
  `SECURITY DEFINER` RPCs, which the same triple-revoke/single-grant pattern as `enqueue_cyl_pipeline_batch`
  already locks down to `bloom_workflows` alone.
- **A batch that fails to submit is terminal, with no retry** — matching PR #469's own explicitly
  deferred retry/requeue gap (tracked there as bloom #605). A transient K8s API blip (not a permanent
  RBAC/config problem) that happens to land during a claim will permanently fail that batch's scans
  today; re-running the original request is the only recovery path in this phase.
- **`ttlStrategy`'s cleanup window is a judgment call, not derived from anything**: too short risks
  deleting a Workflow a human wanted to inspect after a failure; too long lets failed/completed
  Workflows accumulate in a shared namespace. Configured via `WORKFLOWS_K8S_TTL_SECONDS` (see
  tasks.md), not hardcoded inline, so it can be tuned without a code change once there's real usage to
  tune against.
- **A submission that actually succeeded can be recorded as failed, and `ttlStrategy` then erases the
  evidence — found during this review, not fixed here.** If `submit_workflow` succeeds (the K8s API
  creates the Workflow) but the worker crashes before calling `complete_cyl_pipeline_batch`, the pgmq
  message becomes redeliverable; if it's redelivered past `max_reads` without ever settling,
  `claim_cyl_pipeline_batch`'s own poison-message path marks that batch `'failed'` — even though a real
  Workflow is running on the cluster, consuming real GPU time. Because every submission also carries
  `ttlStrategy.secondsAfterCompletion` (this same proposal's own new requirement), that orphaned
  Workflow later self-deletes, leaving no record anywhere that it ran, while the DB permanently says the
  submission failed. **Not fixed in this proposal** — matches the same "terminal for now, no retry"
  posture as the risk above, but is worth flagging separately because it's a false negative, not just a
  missed retry. The mandatory `pipeline-run-id`/`batch-index` labels are what would let a future
  reconciliation pass (Phase 3, or a dedicated sweep) cross-check "failed in the DB but a matching
  Workflow actually exists in the cluster" before TTL deletion — recorded here so that need isn't lost.
- **The mirror-image risk — a real, successful submission gets *resubmitted* — is broader than "worker
  crash," and this proposal has no idempotency check before `submit_workflow` closes it.** Found on
  PR review: `submit_workflow` has no pre-check (by the same `pipeline-run-id`/`batch-index` labels
  the risk above already relies on) for whether a batch was already submitted before creating a new
  Workflow. So it isn't only a crashed worker that can trigger this — any transient failure of the
  `complete_cyl_pipeline_batch` RPC *itself* (not the worker) after a real K8s submission has the same
  effect: the pgmq message is never archived, it redelivers after its visibility timeout, and
  `process_one` submits a **second**, real, GPU-consuming Workflow for the same scan-ids before either
  side settles. Unlike the risk above, the DB can end up looking perfectly consistent afterward
  (`argo_workflow_name` set to whichever submission's `complete()` call won the race) — there is no
  failed status to alert on, just an orphaned first Workflow quietly running to completion and
  self-deleting via `ttlStrategy`. PR review did close the one purely mechanical trigger for this
  (`submit_workflow` previously let a 2xx response with an unparseable body raise an uncaught
  `KeyError` past `process_one`'s except clauses instead of a clean `K8sSubmissionError` — fixed, see
  `k8s_client.py`), but the RPC-failure trigger remains genuinely open. **Not fixed in this
  proposal** — the real fix (query the K8s API for an existing Workflow by label before creating one,
  reusing the same labels the reconciliation-sweep risk above already designates for this purpose) is a
  meaningful `k8s_client.py`/`dispatch_worker.py` addition, not a one-line guard, and is deferred to the
  same future reconciliation work as the risk above rather than rushed in under review pressure.
  Recorded here, explicitly, so it isn't lost or mistaken for "only crashes can cause this." A future
  reconciliation sweep would use the `environment` label (see decision above) alongside
  `pipeline-run-id`/`batch-index` — without it, a sweep can't tell which database's `pipeline-run-id`
  it's looking at, since prod and staging share both the namespace and the `run_id` sequence start.
  Separately: the fix that closed the mechanical `KeyError` trigger routes a 2xx-but-unparseable-body
  response into `fail_batch` — a real submission genuinely occurred, so this is itself a new, narrow
  instance of the *other* "successful-submission-recorded-as-failed" risk above, not this one; it's an
  intentional, accepted tradeoff (a clean, bounded failure mode beats an uncaught exception), not a gap.
- **The real four-`WorkflowTemplate` DAG this phase submits was never itself live-tested against the
  real cluster — only a minimal placeholder was.** Found on PR review: the "Live validation" section
  above tested the REST resource path, credential, and JSON body shape using a single-step `busybox`
  Workflow, not the actual DAG `k8s_client.py`'s `build_workflow_body` constructs (`templateRef`s to
  `sleap-roots-images-downloader-template` → `sleap-roots-predictor-template` →
  `sleap-roots-trait-extractor-template` → `sleap-roots-write-back-template`, with the `scan-ids`
  parameter threaded through). Whether those four `WorkflowTemplate`s are registered exactly as named,
  accept `scan-ids` the way this phase's `build_workflow_body` passes it, and the DAG's dependency
  chain is otherwise well-formed for Argo's controller has not been confirmed end-to-end outside unit
  tests that only assert the Python-side dict shape. **Not fixed in this proposal** — recommend one
  real DAG submission (dry-run or against a disposable/cheap batch) before this worker is enabled in an
  environment receiving real trigger traffic.
- **Deploying the `cyl-pipeline-worker` container ahead of real `WORKFLOWS_K8S_*` secrets is safe in an
  idle environment but destructive in one with live traffic.** With an empty queue the worker never
  reaches the credential check and idles harmlessly. But Phase 1's `POST /workflows/pipeline` is already
  live in staging/prod and enqueues real batches — if the worker container is started there before its
  K8s credentials are actually set, every claimed batch hits the eager config-validation error
  immediately, and (per this phase's terminal-failure design) permanently marks those scans `'failed'`
  with no retry. **This is a deploy-sequencing requirement, not something code can enforce**: the
  `cyl-pipeline-worker` service must not be started in any environment receiving real trigger traffic
  until `WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL` are genuinely set there.

## Migration Plan

Forward-only migration `<timestamp>_add_cyl_pipeline_dispatch_functions.sql` (the three new
`SECURITY DEFINER` wrapper functions and their `EXECUTE` grants only — no table/column changes, and no
new `UPDATE` grant, per the decision above), plus a companion rollback under `supabase/rollbacks/`,
matching this repo's established pattern and Phase 1's own precedent file.

## Open Questions

- The concurrent-duplicate-enqueue race's real fix (see Risks) — surfaced for the approval decision,
  not resolved here.
- The successful-submission-recorded-as-failed gap (see Risks) — surfaced for the approval decision,
  not resolved here; a candidate future fix is a reconciliation sweep using the mandatory labels.
- `WORKFLOWS_K8S_TTL_SECONDS`'s default of `3600` (see decision above) — a starting guess, not derived
  from anything; revisit once there's real submission volume to tune against.
- How `WORKFLOWS_K8S_CA_CERT`'s PEM content becomes an `ssl.SSLContext` for `httpx`'s `verify=`
  parameter (`ssl.create_default_context(cadata=...)` after the un-escape decided above) — an
  implementation detail for `k8s_client.py`, not a further spec-level decision, but worth naming here
  so it isn't rediscovered as a surprise during implementation.
- Whether `dispatch_worker.py` needs more than one replica before real usage exists to justify it —
  pgmq's claim is concurrency-safe (proven by PR #469), so scaling by replica count is a deploy-time
  knob, not a design change, if/when needed.
