## Why

Phase 1 (bloom [#570](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/570), merged
2026-08-03) made `POST /workflows/pipeline` validate, enumerate, dedup-preview, write
`cyl_pipeline_runs`/`cyl_pipeline_run_scans`, chunk into batches, and enqueue every batch onto the
`cyl_pipeline_dispatch` pgmq queue. It deliberately stops there — no code anywhere in this repo reads
that queue or talks to Argo/Kubernetes. Every real pipeline run to date is still a human running
`argo submit` by hand. This proposal implements **Phase 2 of 3** (bloom
[#11](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/11)/[#404](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/404)):
the consumer that actually submits each queued batch to Argo as a Kubernetes `Workflow` CRD, via the
K8s API server directly — `argo submit`'s Argo Server (`:8888`) is in-cluster-only and unreachable
from `bloom-dev`, so this goes straight to the K8s API (`:6443`) instead.

The raw-REST-submission approach was **live-validated against the real cluster** during this
proposal's scoping (not merely designed on paper): a Workflow object was POSTed directly to
`https://<api-server>:6443/apis/argoproj.io/v1alpha1/namespaces/runai-busch-lab/workflows` using the
already-provisioned `bloom-pipeline` ServiceAccount's token+CA, Argo's controller picked it up and ran
it to `Succeeded`, and hand-stamped attribution labels persisted on the object exactly as submitted.
See `design.md`'s "Live validation" section for the full transcript and the one real constraint it
surfaced (the submitting identity cannot `delete` its own Workflows).

## What Changes

- **New dispatch worker** (`services/workflows/dispatch_worker.py`) — a standalone polling process,
  **not** inline in `trigger_pipeline()`. Deliberately modeled on the in-flight bloom PR
  [#469](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/469)
  (`feat/workflows-cyl-video-queue`)'s `worker.py`/`video_queue.py` claim/complete/fail shape — that
  PR's own design doc (`cyl-video-queue-phase2.md`) explicitly says the pipeline-dispatch queue's
  future consumer should copy it, and Phase 1's own `design.md` recorded the same pointer from PR #570's
  review (Benfica, 2026-07-31). Reused wholesale: the visibility-timeout claim, the poison-message
  `read_ct` dead-letter guard, the terminal-failure-for-now convention, graceful `SIGTERM` shutdown, and
  backlog/high-water-mark logging. **Not reused**: anything about rendering — this worker's "work" is a
  single outbound HTTPS POST, not a multi-second ffmpeg encode, so its visibility timeout and
  `stop_grace_period` are both far shorter (see `design.md`).
- **New K8s submission module** (`services/workflows/k8s_client.py`) — constructs the `Workflow` CRD
  body (DAG referencing the four already-registered `WorkflowTemplate`s —
  `sleap-roots-images-downloader-template` → `sleap-roots-predictor-template` →
  `sleap-roots-trait-extractor-template` → `sleap-roots-write-back-template` — parameterized per batch
  by that batch's `scan-ids`, in place of the CLI's `--parameter scan-ids=...`) and POSTs it via
  `httpx` (already a dependency; no new `kubernetes` PyPI client) to the K8s API server. Every submitted
  Workflow is stamped with `submitted-by: bloom-pipeline`, `pipeline-run-id: <run_id>`,
  `batch-index: <n>` — **required**, not optional, per 2026-08-06 cluster-admin feedback: submitting via
  the raw K8s API bypasses Argo Server/the `argo` CLI, so there is no automatic `creator` label. Every
  submitted Workflow also carries `spec.ttlStrategy` for auto-cleanup — **newly required by a real
  constraint found during live validation**: the `bloom-pipeline` ServiceAccount's RBAC is
  `create`/`get`/`list`/`watch` only, with no `delete`, so the submitting code has no other way to keep
  completed Workflows from accumulating.
- **New K8s config, five variables in two groups.** Three real credentials, eagerly validated with no
  safe default (`WORKFLOWS_K8S_TOKEN`, `WORKFLOWS_K8S_CA_CERT` — PEM contents, not a file path, since
  this service has no volume mounts and a read-only rootfs, stored with escaped `\n` sequences to
  survive this repo's line-oriented deploy pipeline — and `WORKFLOWS_K8S_API_URL`), following
  `supabase_client.py`'s exact pattern: module-level `os.environ.get()` reads, an eager all-present
  check before any network call, errors wrapped with `from exc`. Two plain config values with safe
  defaults, never treated as missing (`WORKFLOWS_K8S_NAMESPACE`, default `runai-busch-lab`;
  `WORKFLOWS_K8S_TTL_SECONDS`, default `3600`). Real credential *values* are a deployment step for
  later, out of scope here.
- **Namespace hardcoded to `runai-busch-lab` for v1** — confirmed with the user ("we are always going
  to use busch lab"). No per-request namespace selection; no lab/project column added to
  scans/experiments/waves. Documented as a known v1 limitation, not a data-model change.
- **New migration** adding the `claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/
  `fail_cyl_pipeline_batch` `SECURITY DEFINER` wrapper functions (modeled on PR #469's
  `claim_cyl_video_job`/`complete_cyl_video_job`/`fail_cyl_video_job`) alongside the existing
  `enqueue_cyl_pipeline_batch`, each with the same `EXECUTE`-to-`bloom_workflows`-only pattern. **No
  `UPDATE` grant and no new columns**: all three functions write `argo_workflow_name`/`status`/
  `attempts`/`error_message` on `cyl_pipeline_run_scans` and `status`/`submitted_at`/`completed_at`/
  `error_message` on `cyl_pipeline_runs` as `SECURITY DEFINER` — under the function owner's privileges,
  not the caller's — so `bloom_workflows` needs no direct table grant to write them (see `design.md`'s
  "no direct `UPDATE` grant" decision). Phase 1 already added `argo_workflow_name` (nullable, documented
  as "filled in only once a later phase submits the batch") and every timestamp/error column Phase 2
  needs; this migration is new functions only, no schema change.
- **New docker-compose service** `cyl-pipeline-worker`, built from the same `services/workflows` image
  as the `workflows` API and PR #469's `cyl-video-worker`, `command: ["python", "dispatch_worker.py"]`,
  same hardening (`read_only`, `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs /tmp`).
- **Tests**: unit tests for `dispatch_worker.py`/`k8s_client.py` mocking the claim/submit/complete/fail
  seam (matching PR #469's `test_worker.py` convention — no real K8s or DB needed), plus integration
  tests for the new wrapper functions against the live compose DB (matching
  `test_cyl_pipeline_dispatch.py`'s rollback-wrapped pattern).

**Out of scope for this proposal** (tracked as later work):
- Phase 3 — `GET /workflows/runs/{id}` status polling (a DB read of what this phase writes).
- The Bloom web UI trigger/status panel.
- The concurrent-duplicate-enqueue race Phase 1's `design.md` flagged and explicitly left for Phase 2
  to decide "against real GPU-cost pressure" — **now genuinely costly** (this phase is the point where
  a duplicate enqueue becomes a duplicate GPU submission, not just a duplicate DB row). Not fixed in
  this proposal; see `design.md`'s Risks for why and a recommended minimal follow-up.
- Per-request/per-lab namespace resolution (no data model exists to drive it — see above).
- Retry/requeue of a failed submission (terminal-for-now, matching PR #469's own explicitly-deferred
  gap for the same reason: don't build retry semantics twice, independently, before either queue has
  real production experience).

## Impact

- **Affected specs**: new capability `cyl-pipeline-dispatch` (the worker's claim → submit → record
  behavior, the Workflow CRD shape, attribution labels, `ttlStrategy`, namespace/credential config);
  **modified** capability `cyl-pipeline-runs` (the pgmq-queue requirement is **renamed and extended**
  with the new claim/complete/fail wrapper functions alongside the existing enqueue function; the
  role/grant requirement's *behavior* is unchanged — no `UPDATE` grant is added, see `design.md`'s "no
  direct `UPDATE` grant" decision — but its text is still touched to correct one now-inaccurate
  forward-looking sentence, "a later phase... adds its own `UPDATE` grant," which this phase resolves
  differently than it predicted).
- **Affected code**:
  - `supabase/migrations/<timestamp>_add_cyl_pipeline_dispatch_functions.sql` (new — re-verify the
    latest migration timestamp on `origin/staging` immediately before implementation)
  - `supabase/rollbacks/<timestamp>_add_cyl_pipeline_dispatch_functions_rollback.sql` (new)
  - `services/workflows/dispatch_worker.py`, `services/workflows/k8s_client.py`,
    `services/workflows/pipeline_queue.py` (new modules)
  - `services/workflows/tests/test_dispatch_worker.py`, `services/workflows/tests/test_k8s_client.py`
    (new, unit)
  - `tests/integration/test_cyl_pipeline_dispatch.py` (extended — the existing file Phase 1 created for
    this same queue; **not** a new file, see `tasks.md`)
  - `docker-compose.dev.yml`, `docker-compose.prod.yml` (new `cyl-pipeline-worker` service)
  - `tests/unit/test_env_defaults.py`-covered files: the three real credentials
    (`_TOKEN`/`_CA_CERT`/`_API_URL`) go in that test's `SENSITIVE_INVENTORY`, matching how
    `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD` were already handled; the two defaulted config values
    (`_NAMESPACE`/`_TTL_SECONDS`) instead get real entries in `.env.prod.defaults` **and**
    `.env.staging.defaults`, since neither is a secret and both need the same key present in both
    files. Getting this classification wrong for even one variable fails the env-parity CI job.
  - `services/workflows/README.md` (document the new worker, credential env vars, namespace config,
    and correct three existing forward-looking/stale sentences — see `tasks.md` §6.1)
  - `_WIKI/SUPABASE/README.md` (correct the existing "not `UPDATE`... a later phase adds it" sentence
    to instead explain why no `UPDATE` grant was needed, and document the three new wrapper functions)
- **Related, not modified here**: bloom PR #469 (unmerged pgmq video-worker precedent — referenced as
  the structural template, not depended on as code); the `bloom-pipeline`/`bloom-workflow`
  ServiceAccounts and their RBAC (already provisioned and live-validated in `runai-busch-lab`, not
  provisioned or changed by this proposal); Phase 3 (`GET /workflows/runs/{id}`, not started).
