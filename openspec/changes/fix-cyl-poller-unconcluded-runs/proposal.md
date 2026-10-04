## Why

The cylinder status poller asks Argo for each workflow's phase every cycle and remembers
nothing between cycles. Argo deletes a finished workflow `WORKFLOWS_K8S_TTL_SECONDS` (3600 s)
after it ends (`ttlStrategy`), after which a lookup 404s. From then on the poller has no record
of how that workflow ended (bloom#1042):

1. **Rows that stay `'queued'` forever.** Since #1038, bloomctl leaves a workflow's unresolved
   rows to the poller when write-back's last attempt failed retriably. If the poller never sees
   that workflow's terminal phase before GC (poller downtime, or `K8sStatusError` every cycle
   for longer than the TTL, as with the CA-cert breakage in #1019), then:
   - if every workflow of the run 404s, `rollup([])` is `None` and the sweep skips the run;
   - if every other workflow succeeded, `'complete'` is withheld every cycle.

   Either way the rows stay `'queued'` and the run stays `'running'`.
2. **A withheld-`'complete'` run stalls.** A multi-batch run whose early batch is GC'd before
   the last batch finishes never concludes, and its `done_count`/`failed_count` freeze at the
   last `'running'` write. The runs list reads those stale columns.
3. **A poller-concluded `'partial'` run is re-polled forever.** `'partial'` stays a candidate
   and a valid source status, so every cycle rewrites `completed_at`. Once the successful
   workflow is GC'd, the rollup sees only `'Failed'` and flips the run `partial` → `failed`
   while its written scans remain.

Separately, the run page's "Re-run failed scans" offers rows whose result arrived after they
were closed (the row's `lateResultNote`). Re-running them spends GPU time on results Bloom
already has and writes a duplicate trait source.

A time-based rule ("404'd and dispatched more than TTL + 5 min ago") was tried in #1038 and
reverted: GC is TTL after a workflow *finishes*, not after dispatch, and a 404 that isn't GC (a
namespace change, a re-pointed API URL, a CRD reinstall, a proxy) would permanently fail live
rows.

## What Changes

- **Bloom records each workflow's terminal phase.**
  - A new table `cyl_pipeline_run_workflows` holds one row per `(run_id, argo_workflow_name)`
    with the last terminal phase the poller saw (`Succeeded`, `Failed` or `Error`).
  - The poller writes it through a new least-privilege RPC
    `record_cyl_pipeline_workflow_phase` whenever a live lookup returns a terminal phase that
    differs from the stored one.
  - When a later lookup 404s, the poller uses the stored phase. A live phase always wins over a
    stored one (an operator `argo retry` can revive a finished workflow).
  - This alone resolves items 2 and 3, and item 1 whenever the poller was up while the
    workflow was alive.
- **A 404 counts as "gone" only when Kubernetes says so.**
  - `get_workflow_status` returns `None` only when the 404 body is a Kubernetes `Status` with
    `reason: NotFound` whose `details` name this exact workflow (`name`, `kind: workflows`,
    `group: argoproj.io`).
  - Any other 404 raises `K8sStatusError`, as any other failed lookup does today. That covers a
    proxy page, a missing CRD and a wrong API path.
  - `get_workflow` and its RNA-seq callers keep their current behaviour.
- **A workflow that is gone and was never seen finishing is eventually concluded.**
  - That means a verified NotFound with no stored phase. The poller tracks how long it has been
    NotFound, in memory, reset by any other result for that name.
  - The workflow counts as **removed** only when both of these hold:
    - at least 3 consecutive verified NotFound cycles spanning at least
      `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` (default 600);
    - at least `WORKFLOWS_K8S_TTL_SECONDS` have passed since the newest `created_at` among its
      rows. Rows are created before dispatch, so GC cannot have happened sooner. This keeps a
      wrong namespace from failing any run younger than the TTL.

    Once removed:
    - its still-`'queued'` rows are closed `'failed'` with a distinct message saying the
      workflow was removed before Bloom saw it finish;
    - its effective phase is then derived from its rows: `Succeeded` when every row is
      `'written'`/`'reused'`, otherwise `Failed`.
  - Before the grace period ends it stays "unknown", exactly as a 404 is today.
- **A poller-concluded run is final.**
  - A new column `cyl_pipeline_runs.poller_concluded_at` is stamped by
    `update_cyl_pipeline_run_status` on its first terminal write.
  - The RPC refuses any later write to a run with that column set, and the poller stops
    selecting such runs.
  - A dispatch-settled `'partial'` run (column still `NULL`) still gets its first real
    confirmation from Argo, as today.
  - `completed_at` is stamped once, by that first poller conclusion.
- **The run page stops offering re-runs of rows that already have this run's result.**
  - "Re-run failed scans" and "Re-run scans without a result" leave out failed rows whose late-result
    note is shown.
  - Their counts and the submitted `scan_ids` match.

## Impact

- **Affected specs:**
  - `cyl-pipeline-status-polling`: MODIFIED the `get_workflow_status`, poller, rollup,
    `update_cyl_pipeline_run_status` and "`'complete'` does not imply…" requirements;
    ADDED the workflow-phase record requirement.
  - `cyl-pipeline-ui`: MODIFIED "Run actions are offered…".
- **Affected code:**
  - `supabase/migrations/`: one new migration (table, column, two RPCs), plus its rollback in
    `supabase/rollbacks/`.
  - `services/workflows/`:
    - `k8s_client.py`: NotFound verification for `get_workflow_status`.
    - `status_poller.py`: stored phases, the removed-workflow grace tracker, final runs.
    - `tests/test_k8s_client.py`, `tests/test_status_poller.py`.
    - `README.md`.
  - `docker-compose.dev.yml` / `docker-compose.prod.yml`: pass `WORKFLOWS_K8S_TTL_SECONDS` to
    `cyl-status-poller` (today it is submission-only); the grace variable uses the code default.
  - `web/`:
    - `app/app/cyl-pipeline-runs/[runId]/RunDetailLive.tsx` and its test.
    - `lib/database.types.ts`: hand-edited for the new column and table.
  - `tests/integration/`: the status-polling and new workflow-phase RPC tests.
- **Ordering.** `fix-cyl-writeback-retry-reconcile` (#1038, unarchived) MODIFIES the same
  poller requirement. This change's delta is written on top of that change's text, so that
  change MUST be archived first.
- **Not included:**
  - `activeDeadlineSeconds` at dispatch: it collides with `isolate-cyl-pipeline-environments`'
    "six overrides" requirement and needs a per-workflow dispatch time.
  - An `onExit` or exit-gate callback into Bloom: it needs sleap-roots-pipeline and bloomctl
    changes and a pin bump. A possible follow-up if poller-downtime gaps recur.
  - Repairing existing stuck runs by hand: the new poller concludes them on its own after
    deploy (see design.md, Migration).
  - The RNA-seq poller's own 404 handling.
