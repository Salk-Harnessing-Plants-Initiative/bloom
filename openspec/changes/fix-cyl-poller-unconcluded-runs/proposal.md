## Why

The cylinder status poller keeps no record of how a workflow ended. Argo deletes a finished workflow
`WORKFLOWS_K8S_TTL_SECONDS` after it ends, so later lookups 404. From then on, runs and rows can stop
short of a final status for good (bloom#1042):

1. A run whose workflow was deleted before the poller saw it finish keeps that workflow's rows
   `'queued'` and stays `'running'`. This covers poller downtime, or a broken Kubernetes lookup that
   lasts longer than the TTL (#1019).
2. In a multi-batch run whose early batch was deleted, `'complete'` is withheld every cycle and the
   counts freeze.
3. Once the poller concludes a run `'partial'`, it keeps re-polling it and re-stamping
   `completed_at`, and flips it to `'failed'` after the successful workflow is deleted.

On the run page, "Re-run failed scans" also offers rows whose result arrived after they were closed.
Re-running them spends GPU time and writes a duplicate trait source.

## What Changes

- **Store each workflow's terminal phase.** This covers items 2 and 3's flip, and item 1 whenever
  the poller saw the workflow finish.
  - A new table, `cyl_pipeline_run_workflows`, keeps the last terminal phase the poller saw per run
    and workflow, written by a new RPC.
  - A later NotFound falls back to the stored phase. A live phase always wins over a stored one.
- **Treat a 404 as "gone" only when Kubernetes names that workflow.**
  - `get_workflow_status` checks the NotFound body, and the workflow's `pipeline-run-id` label.
  - Any other 404 is a lookup error.
  - `get_workflow` and the RNA-seq poller are unchanged.
- **Conclude a workflow that was never seen finishing, but only after a guarded wait.** This covers
  the rest of item 1.
  - It counts as removed after repeated verified NotFounds over a grace period, and only once its
    rows are older than the TTL.
  - Its `'queued'` rows are then closed by a new run-scoped RPC, and its phase is derived from its
    rows.
  - While any workflow is still unresolved, the run gets **no** terminal status.
- **Make a poller conclusion final** (item 3).
  - `update_cyl_pipeline_run_status` stamps a new column, `cyl_pipeline_runs.poller_concluded_at`,
    once, and ignores later writes.
  - A `'partial'` that dispatch settled still gets one real confirmation.
- **On the run page, leave rows already holding this run's late result out of both re-run
  actions.**

Rules and rationale: design.md D1–D8. Normative text: the spec deltas.

## Impact

- **Affected specs:**
  - `cyl-pipeline-status-polling`: MODIFIED `get_workflow_status`, the poller, the rollup rule,
    `update_cyl_pipeline_run_status`, and "`'complete'` does not imply…"; ADDED the workflow-phase
    table and RPCs.
  - `cyl-pipeline-runs`: MODIFIED "`cyl_pipeline_runs` table" (the new column).
  - `cyl-pipeline-ui`: MODIFIED "Run actions are offered…".
- **Two PRs** (design D7):
  - **PR A, database:**
    - this proposal;
    - `supabase/migrations/<ts>_add_cyl_pipeline_run_workflows.sql` and its rollback;
    - `web/lib/database.types.ts`, edited by hand;
    - `_WIKI/SUPABASE/erd.md`;
    - `tests/integration/test_cyl_pipeline_run_workflows.py` (new),
      `test_cyl_pipeline_status_polling.py` and `test_cyl_writeback_rpc.py`.
  - **PR B, code, opened after PR A is on staging:**
    - `services/workflows/`: `k8s_client.py`, `status_poller.py`, their tests and `README.md`;
    - `docker-compose.dev.yml` and `docker-compose.prod.yml`, adding the TTL to `cyl-status-poller`
      and `rnaseq-status-poller`;
    - `.env.*` comments;
    - `web/`: `RunDetailLive.tsx` and its test, and comments in `lib/cyl-pipeline/`
      (`run-display.ts`, `realtime-reducer.ts`, `failure-hints.ts` and its test);
    - `tests/unit/test_rnaseq_status_poller_container.py`, unchanged but must still pass.
- **Ordering:** `fix-cyl-writeback-retry-reconcile` (#1038, unarchived) MODIFIES the same poller
  requirement, and this delta is written on its text. That change MUST be archived first.
- **Not included:**
  - `activeDeadlineSeconds` at dispatch, and an `onExit`/exit-gate callback to Bloom (design,
    Alternatives).
  - The RNA-seq poller's 404 handling.
  - Offering "Re-run scans without a result" on `'partial'` runs.
