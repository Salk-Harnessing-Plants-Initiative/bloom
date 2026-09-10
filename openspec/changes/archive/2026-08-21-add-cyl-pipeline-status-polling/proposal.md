## Why

Phase 1 (bloom [#570](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/570)) enumerates,
dedups, and enqueues; Phase 2 (bloom [#677](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/677),
`add-cyl-pipeline-dispatch`) actually submits each queued batch to Argo as a real `Workflow` CRD. Once
submitted, nothing ever looks at it again. `_settle_cyl_pipeline_run` (added in Phase 2's migration
`20260817120000_add_cyl_pipeline_dispatch_functions.sql`) only ever resolves `cyl_pipeline_runs.status`
to `'submitted'`, `'failed'`, or `'partial'` — all three describe **dispatch** outcome (did the K8s API
accept the submission), not the pipeline's real result. `'running'` and `'complete'` are valid per the
table's own `CHECK` constraint (added in Phase 1's migration `20260730120000_create_cyl_pipeline_runs.sql`)
but nothing has ever set them. This is the last piece of bloom
[#11](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/11) — **Phase 3 of 3**.

The canonical architecture (sleap-roots-pipeline's
[2026-07-06 A4 design doc](https://github.com/talmolab/sleap-roots-pipeline/blob/main/docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md),
§3 step 4) is explicit about the shape: *"workflows service **polls Argo** → updates
`cyl_pipeline_runs.status`; ... Browser watches `cyl_pipeline_runs` via **Supabase Realtime**"* — the
poller lives in Bloom's `workflows` service and drives the write; the browser never polls, it
subscribes. §4 lists `GET /workflows/runs/{id}` only as "if needed" — a plain DB read, not the thing
that talks to Argo. This proposal builds both: the poller (the actual gap) and the GET route (cheap,
literally what the issue is titled after, useful for any caller without a websocket, e.g. `bloomctl` or
a script).

**Explicitly out of scope**: per-scan `cyl_pipeline_run_scans.status` (`predicted`/`written`/`reused`).
The design doc's own §3 step 3 says that transition happens from the `traits+writeback` stage calling
`insert_cyl_result_envelope`, not from Argo-phase polling — and confirmed by grep, no migration for that
RPC (`20260630180000`/`20260706170000`/`20260720000000`) ever touches `cyl_pipeline_run_scans` at all.
An Argo phase of `Succeeded` only proves the pod exited 0; it cannot distinguish `predicted` from
`written` from `reused` for an individual scan in a batch. Filed as its own gap:
[bloom #696](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/696).

**Dependency note**: this proposal's spec deltas are written against the baseline
`add-cyl-pipeline-dispatch` produces once archived — i.e. the `cyl-pipeline-dispatch` capability and the
`cyl-pipeline-runs` capability's Phase-2 `MODIFIED` text. That archive PR
([bloom #688](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/688)) is **approved but not
yet merged** as of this proposal. Per user decision, this proposal proceeds against #688's already-known
diff rather than waiting; `openspec archive` for this change should not run until #688 has actually
landed.

## What Changes

- **New standalone poller** (`services/workflows/status_poller.py`) — a separate process, same
  claim-nothing/sweep-everything shape difference from `dispatch_worker.py` that its job requires:
  `dispatch_worker.py` reacts to new pgmq messages; this poller instead periodically re-checks every
  run still in `'submitted'`/`'running'` status (`WORKFLOWS_STATUS_POLL_SECONDS`, default 15), fetches
  the real Argo phase for each of that run's distinct `argo_workflow_name`s via a new
  `k8s_client.get_workflow_status()`, and writes the recomputed run-level status back via a new
  `SECURITY DEFINER` RPC. Modeled on `dispatch_worker.py` for everything else it already got right:
  graceful `SIGTERM`/`SIGINT` shutdown (finish the in-flight sweep, don't start a new one), retry-with-
  backoff on the startup Supabase connection (not a one-shot `app_client()` call that crash-loops on a
  transient outage), and a tight `DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS`-style bound on its RPCs.
- **New `k8s_client.get_workflow_status(name)`** — `GET
  {API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows/{name}`, reading
  `.status.phase`. Returns `None` on `404` (the Workflow no longer exists — most often `ttlStrategy`
  already cleaned it up; an expected condition, not a failure) and raises a new `K8sStatusError` (same
  sanitized-message convention as `K8sSubmissionError` — a fixed, generic message; the real detail is
  logged server-side only) for any other non-2xx response or network-level failure.
  `_validate_config()`/`_ssl_context()`/`TOKEN`/`API_URL`/`NAMESPACE` are all reused unchanged.
- **Rollup logic mirrors `_settle_cyl_pipeline_run`'s existing three-way `CASE`, extended with a fourth
  status-producing branch for real, non-terminal Argo phases (plus a rule-0 empty-list guard ahead of
  all four)** — the full rule (effective-phase derivation, all five rules, and the `'partial'`/
  `'failed'` value reuse) is specified once, normatively, in this change's `cyl-pipeline-status-polling`
  spec delta ("Rollup rule..." requirement); not restated here. Computed in Python (the poller), not
  SQL — see `design.md`'s decision for why this is safe despite Phase 2's own "aggregate in SQL, not
  Python" precedent.
- **New migration**: `update_cyl_pipeline_run_status(p_run_id bigint, p_status text) RETURNS void`,
  `SECURITY DEFINER`, validating `p_status` against the same four values the `CHECK` constraint already
  allows for this transition (`'running'|'complete'|'failed'|'partial'`), guarded to only ever write a
  run currently in `'submitted'`/`'running'` (a run already `'queued'` was never dispatched — nothing
  for this poller to say about it yet; a run already `'complete'`/`'failed'`/`'partial'` is terminal —
  this poller's own candidate-selection query already excludes it, and the guard is defense-in-depth,
  not the only thing preventing a regression). Sets `completed_at = now()` on the transition into a
  terminal status if not already set. `EXECUTE` revoked from `PUBLIC`/`anon`/`authenticated`, granted
  only to `bloom_workflows` — the same triple-revoke/single-grant pattern every prior `SECURITY DEFINER`
  wrapper in this program uses.
- **New route** `GET /workflows/runs/{run_id}` (`services/workflows/main.py`) — a plain, cheap read of
  current DB state (the run row plus its scan rows), auth-gated the same way the two existing routes
  are (`Depends(require_supabase_user)` + `enforce_rate_limit`). Does **not** itself talk to Argo —
  live reconciliation only ever happens inside the poller. 404 if the run doesn't exist.
- **New docker-compose service** `cyl-status-poller`, same image/build context as `workflows` and
  `cyl-pipeline-worker`, `command: ["python", "status_poller.py"]`, same hardening (`read_only`,
  `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs /tmp`). Needs the same three real K8s credentials
  (`WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL`, read-only use this time — `get`, not `create`) plus the
  existing `WORKFLOWS_SUPABASE_*` vars.
- **Tests**: unit tests for `k8s_client.get_workflow_status`/`status_poller.py`'s rollup logic (mocking
  the DB read + Argo GET seam, matching `test_dispatch_worker.py`'s convention — no real K8s/DB), a
  route-wiring test for the new GET endpoint (`TestClient` + `dependency_overrides`, matching
  `test_pipeline.py`'s convention), and integration tests for the new RPC against the live compose DB
  (new file, matching `test_cyl_pipeline_dispatch.py`'s rollback-wrapped `pg_conn` pattern).

**Out of scope for this proposal** (tracked as later work):
- Per-scan `cyl_pipeline_run_scans.status` (`predicted`/`written`/`reused`) — [bloom
  #696](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/696), a separate mechanism
  (the write-back RPC), not this poller.
- Reconciling a run whose scans are `status = 'failed'` with **no** `argo_workflow_name` at all, where
  the underlying submission actually succeeded (the "successful-submission-recorded-as-failed" risk
  Phase 2's `design.md` flagged) — this poller has no workflow name to poll for those scans in the
  first place; a real fix needs a label-based `LIST`, not a by-name `GET`, and is deferred to a future
  reconciliation sweep, matching Phase 2's own deferral.
- A Workflow that's TTL-deleted before this poller ever observes a terminal phase for it (a real gap if
  `WORKFLOWS_STATUS_POLL_SECONDS` were ever configured close to or above `WORKFLOWS_K8S_TTL_SECONDS`) —
  documented as an operational constraint (`design.md`), not solved in code.
- The Bloom web UI status panel (Realtime subscription, per the design doc's §10) — not this repo's
  scope until a UI proposal exists.

## Impact

- **Affected specs**: new capability `cyl-pipeline-status-polling` (the poller, the rollup rule, the
  new RPC, the new GET route); **modified** capability `cyl-pipeline-runs` (a `MODIFIED` delta
  correcting the `status` column's description — Phase 2 already documented `running`/`complete` as
  reachable-but-unset; this phase is what finally sets them, so the "nothing sets them yet" language
  is now stale, the same kind of forward-looking-sentence correction Phase 2 itself applied to Phase
  1's text).
- **Affected code**:
  - `supabase/migrations/<timestamp>_add_cyl_pipeline_run_status_polling.sql` (new — re-verify the
    latest migration timestamp on `origin/staging` immediately before implementation)
  - `supabase/rollbacks/<timestamp>_add_cyl_pipeline_run_status_polling_rollback.sql` (new)
  - `services/workflows/k8s_client.py` (extended — `get_workflow_status`, `K8sStatusError`)
  - `services/workflows/status_poller.py` (new)
  - `services/workflows/main.py` (new `GET /workflows/runs/{run_id}` route)
  - `services/workflows/supabase_client.py` (rename `DISPATCH_WORKER_POSTGREST_TIMEOUT_SECONDS` →
    a consumer-neutral name, now that the poller is its second consumer);
    `services/workflows/dispatch_worker.py` (existing import/usages updated to match, unrelated to
    this phase's own behavior otherwise); `services/workflows/tests/test_supabase_client.py`
    (existing assertions updated to match)
  - `services/workflows/tests/test_k8s_client.py` (extended), `services/workflows/tests/test_status_poller.py`
    (new), `services/workflows/tests/test_pipeline.py`-style route test added to a new or existing test
    module for `main.py` (see `tasks.md` for the exact file)
  - `tests/integration/test_cyl_pipeline_status_polling.py` (new — a distinct RPC/migration from
    Phase 2's pgmq functions, not an extension of `test_cyl_pipeline_dispatch.py`)
  - `docker-compose.dev.yml`, `docker-compose.prod.yml` (new `cyl-status-poller` service)
  - `tests/unit/test_env_defaults.py`: no new entries needed. `WORKFLOWS_WORKER_POLL_SECONDS` (the
    precedent) does not appear in either compose file's `environment:` block at all — confirmed by
    grep, not assumed — so its Python-side default (`os.environ.get(..., "5")`) is what actually
    governs it, and it's invisible to both `SENSITIVE_INVENTORY` and the default-file tests.
    `WORKFLOWS_STATUS_POLL_SECONDS` follows the identical path: it SHALL NOT be referenced via `${...}`
    in either compose file's `environment:` block for `cyl-status-poller` (see `tasks.md` §6.1) —
    referencing it there without a real default-file entry would make
    `docker-compose --env-file` inject an empty string and silently override the code default, the
    exact `NAMESPACE`-empty-string trap Phase 2's own `design.md` already documented and fixed for a
    different variable.
  - `services/workflows/README.md` (document the poller, the new GET route, and the rollup rule)
- **Related, not modified here**: `dispatch_worker.py`/`pipeline_queue.py` (unchanged — this poller is
  a fully separate process and RPC surface); the four `WorkflowTemplate`s and their DAG (unchanged);
  bloom #696 (the sibling per-scan write-back gap, filed but not implemented here); bloom PR #688 (the
  Phase 2 archive PR this proposal's spec deltas assume, not yet merged).
