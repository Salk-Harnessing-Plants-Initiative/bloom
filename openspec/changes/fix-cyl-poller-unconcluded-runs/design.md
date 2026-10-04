## Context

The cylinder status poller (`services/workflows/status_poller.py`) reads every candidate run's
scan rows, asks Argo for each distinct workflow's phase, rolls the phases up into a run status
and writes it through `update_cyl_pipeline_run_status`. It keeps no state between cycles. Argo
deletes a finished workflow `WORKFLOWS_K8S_TTL_SECONDS` after it ends, and from then on the
lookup 404s. Every gap in bloom#1042 comes from that loss of history:

| Gap | Today's code path |
|---|---|
| All workflows 404 | `rollup([])` is `None` → `continue` (`sweep_once`) |
| Some 404, rest `Succeeded` | `'complete'` withheld → `continue`, no counts written |
| `'partial'` re-polled | `_fetch_candidate_runs` selects `'partial'`; RPC accepts it as a source status |
| `partial` → `failed` | GC'd `Succeeded` sibling drops out of `phases`, leaving only `Failed` |

`get_workflow` returns `None` on **any** 404 without reading the body, so a proxy page or a
missing CRD looks the same as a GC'd workflow.

Earlier decisions this builds on:
- `fix-cyl-pipeline-run-scan-status` Decision 6, addenda 5, 7 and 8: the terminal-rollup backstop
  reconciles 404'd workflows too, and gating it on `any_unknown` was reverted because a
  GC'd sibling then stalled the run forever.
- `fix-cyl-writeback-retry-reconcile` D2: a 404 is never "settled" while the run runs. A
  dispatch-age rule was tried and reverted.

## Goals / Non-Goals

**Goals**
- Every run whose workflows have all finished eventually concludes, with no row left `'queued'`.
- A workflow the poller saw finish keeps that outcome after GC.
- A 404 that is not Kubernetes saying "this workflow does not exist" never closes rows.
- A run the poller concluded is never re-polled, re-stamped or downgraded.
- The run page never offers to re-run a scan that already has this run's result.

**Non-Goals**
- Callbacks from inside the workflow (`onExit`, exit-gate) or `activeDeadlineSeconds`.
- Changing the write-back guard (a `'failed'` row stays `'failed'`).
- The RNA-seq poller's 404 handling.

## Decisions

### D1. Store each workflow's terminal phase in a new table

`cyl_pipeline_run_workflows`:

| Column | Type | Notes |
|---|---|---|
| `run_id` | `BIGINT NOT NULL REFERENCES cyl_pipeline_runs(id)` | |
| `argo_workflow_name` | `TEXT NOT NULL` | |
| `phase` | `TEXT NOT NULL CHECK (phase IN ('Succeeded','Failed','Error'))` | last terminal phase seen |
| `observed_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | when that phase was first recorded |
| | `PRIMARY KEY (run_id, argo_workflow_name)` | |

- Keyed by run and name, not name alone. `generateName` collisions are possible (addendum 5).
- RLS follows `cyl_pipeline_run_scans`:
  - read for `bloom_workflows`, `bloom_user`, `bloom_agent`;
  - all for `bloom_admin`;
  - no direct write grant to `bloom_workflows`.
- Writes go through `record_cyl_pipeline_workflow_phase(p_run_id BIGINT,
  p_argo_workflow_name TEXT, p_phase TEXT) RETURNS BOOLEAN`, `SECURITY DEFINER`, EXECUTE only
  for `bloom_workflows`. The RPC:
  - raises on a phase outside the three terminal values;
  - inserts or updates only when some `cyl_pipeline_run_scans` row has that `run_id` and
    `argo_workflow_name`, so a typo can't create an orphan;
  - on conflict updates `phase` and `observed_at` only when the phase differs;
  - returns whether a row was written.
- The poller reads the run's stored phases with one `select` per run per cycle. It calls the
  RPC only when a live lookup returns a terminal phase different from the stored one, so a
  steady state costs no writes.

**Why not a column on `cyl_pipeline_run_scans`:** the phase belongs to the workflow, not the
scan. Storing it there means writing ~25 rows per workflow and reading back a value that is
duplicated across them.

**Why the live phase wins:** `argo retry` can move a `Failed` workflow back to `Running`. The
stored value is only a fallback for a 404, and is overwritten when the workflow finishes again.

**Failure handling:** if the RPC fails, the cycle is marked unclean (`PGRST202` stays clean, as
elsewhere) and this cycle's live phase is still used. Nothing is lost: the next cycle retries
while the workflow still exists.

### D2. Verify a 404 before calling it NotFound

- `get_workflow_status` parses a 404 body. It returns `None` only when the body is a JSON object
  with:
  - `kind == "Status"`
  - `reason == "NotFound"`
  - `details.name == name`
  - `details.kind == "workflows"`
  - `details.group == "argoproj.io"`
- Any other 404 logs the status and body server-side and raises `K8sStatusError` with the
  existing generic message. This covers a non-JSON body, a different reason, or details naming
  something else. A missing CRD returns a `Status` without the workflow's name; a proxy returns
  HTML.
- `get_workflow` and the RNA-seq poller keep their current behaviour (`None` on any 404). The
  verification lives in a private helper that `get_workflow_status` calls.

A wrong **namespace** still returns a verified NotFound for the exact name. D3's age guard is
what limits that case.

### D3. "Removed": verified NotFound, held through a grace period, past the earliest possible GC

The poller keeps an in-memory map from workflow name to the first verified-NotFound time
(`time.monotonic()`) and a consecutive count.
- Any other outcome for that name resets its entry: a live phase, or an error for the run.
- A name that no longer appears in any candidate run is dropped.

A workflow with no stored phase is **removed** only when all three hold:
1. its consecutive verified NotFound count is ≥ 3;
2. at least `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` (default 600; non-positive or malformed values
   fall back with a warning, matching `_resolve_poll_interval`) have passed since the first one;
3. `now() - max(created_at of its rows) ≥ WORKFLOWS_K8S_TTL_SECONDS`.

Condition 3 is a **necessary** condition only, unlike #1038 round 1's sufficient one:
- The rows are inserted before dispatch, and GC happens TTL after completion, so GC cannot
  happen before `created_at + TTL`.
- A NotFound earlier than that is certainly not GC, so a fresh run is never touched by a
  namespace or URL mistake.
- The poller reads the TTL from the same `WORKFLOWS_K8S_TTL_SECONDS` variable the dispatcher
  uses, now passed to `cyl-status-poller` in both compose files.

**Why in memory, not in the DB:** a restart only delays a conclusion by one grace period, and
the scenario is already hours late. Persisting the count would add a write every cycle for every
404'd workflow.

**Residual risk:** a namespace or URL change that leaves Kubernetes answering NotFound would,
after the grace period, conclude every still-running run older than the TTL, failing its
`'queued'` rows. This is accepted:
- the K8s API answering NotFound for every workflow is loud in the logs (one warning per
  workflow per cycle);
- results written later still land as data and show the late-result note (D5);
- the alternative leaves real stuck runs unconcluded forever.

### D4. A removed workflow's effective phase comes from its rows

Once a workflow is removed:
- its still-`'queued'` rows are closed by `fail_cyl_pipeline_run_scans_without_result` with
  this message: "the workflow was removed before Bloom saw it finish; check whether a result
  file exists before re-running prediction";
- it then counts in the rollup as `Succeeded` if every one of its rows is `'written'` or
  `'reused'`, and as `Failed` otherwise.

The phase is not stored: after close-out the rows are final, so re-deriving it gives the same
answer. A removed workflow is settled (closed while the run runs, like a terminal phase) and no
longer makes `any_unknown` true.

`any_unknown` now means at least one workflow is in its grace period: a verified NotFound with
no stored phase that is not yet removed. The withheld-`'complete'` rule and the terminal-rollup
backstop keep their current meaning.

### D5. A poller conclusion is final: `poller_concluded_at`

- New column: `cyl_pipeline_runs.poller_concluded_at TIMESTAMPTZ NULL`.
- `update_cyl_pipeline_run_status`:
  - matches rows `WHERE status IN ('submitted','running') OR (status = 'partial' AND
    poller_concluded_at IS NULL)`;
  - on a terminal `p_status`, sets `completed_at = now()` and `poller_concluded_at = now()`.
- `_fetch_candidate_runs` selects `id, status, poller_concluded_at` for the three statuses and
  keeps a `'partial'` row only when `poller_concluded_at` is null. This is filtered in Python;
  the fake client has no `or_`.
- A dispatch-settled `'partial'` (`_settle_cyl_pipeline_run`, column `NULL`) is still confirmed
  once.

**Why a column and not `completed_at`:** `_settle_cyl_pipeline_run` stamps `completed_at` for
`'submitted'` and `'partial'` too, so it can't tell "dispatch guessed" from "poller confirmed".

**Rows from before this change:** the migration leaves the column `NULL` everywhere. A run the
old poller already concluded `'partial'` therefore gets one more confirmation and is then final.
Thanks to D4 that confirmation can no longer flip it to `failed` because a sibling was GC'd.

### D6. Run page: leave late-result rows out of the re-run actions

In `RunDetailLive.tsx`, `failedIds` and `unresultedIds` exclude rows whose computed
`lateResultNote` is non-null. The button counts come from these lists, so they match.
- When the latest-source or source-runs lookup failed, the note is null, so every failed row is
  offered, as today.
- A row that turns `failed` live is offered until its batched lookup lands (≤ 500 ms), then
  drops out.
- `settled` and the header counts are unchanged: the row is still `failed`.

## Risks / Trade-offs

- **Namespace misconfiguration** (D3 residual risk): bounded to runs older than the TTL; loud
  in the logs.
- **Rows-derived phase is an approximation.** A removed workflow whose producers exited `3`
  (gate `Succeeded`) but whose isolated scans are `'failed'` counts as `Failed`, not
  `Succeeded`. The run then reads `'partial'`/`'failed'` instead of `'complete'`. The per-scan
  counts are exact either way, and the spec already says to read counts, not status.
- **Deploy ordering.** App code deploys before migrations. Until the migration applies:
  - the phase select fails → that run's check is isolated and the cycle is unclean;
  - the record RPC and the five-column status RPC return `PGRST202` → logged quietly.

  So the poller falls back to the stored-nothing behaviour for at most the deploy window.
- **More rows read per cycle**: one extra small `select` per candidate run.

## Migration

- Forward-only migration `2026100312xxxx_add_cyl_pipeline_run_workflows.sql`:
  - creates the table, RLS, grants and the record RPC;
  - adds `poller_concluded_at`;
  - re-creates `update_cyl_pipeline_run_status` with the same signature.
- Companion rollback in `supabase/rollbacks/`.
- After deploy the poller concludes runs already stuck by #1042 on its own, once condition 3
  and the grace period hold, which is immediate for old runs plus ~10 min. Before promoting,
  run a read-only query on staging and prod listing the runs it will conclude, and record them
  in the PR.

## Open Questions

None. The mechanism (D1), the never-seen rule (D3), finality (D5) and the UI exclusion (D6)
were chosen by the user on 2026-10-03.
