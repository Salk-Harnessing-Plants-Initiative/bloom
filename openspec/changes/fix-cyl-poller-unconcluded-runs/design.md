## Context

`services/workflows/status_poller.py` reads a run's scan rows, asks Argo for each workflow's
phase, rolls the phases up and writes the result. It keeps nothing between cycles. Argo's
`ttlStrategy` deletes a finished workflow `WORKFLOWS_K8S_TTL_SECONDS` after it ends. After that,
`get_workflow` returns `None` on **any** 404 without reading the response body.

This change builds on two earlier decisions:

- `fix-cyl-pipeline-run-scan-status`, Decision 6 and addenda 5, 7 and 8: the terminal-rollup
  backstop. Gating it on `any_unknown` was reverted because a garbage-collected sibling then stalled
  the run forever.
- `fix-cyl-writeback-retry-reconcile` (#1038) D2: a 404 is never "settled". A rule based on dispatch
  age was tried and reverted.

The normative rules are in the spec deltas. This file gives the reasons for them.

## Goals / Non-Goals

**Goals**

- A run whose workflows have all ended eventually concludes, with no row left `'queued'`.
- A workflow the poller saw finish keeps that outcome after Argo deletes it.
- Only Kubernetes saying "this workflow, for this run, does not exist" can close rows.
- A run the poller concluded is never re-polled, re-stamped or downgraded.
- The run page never offers to re-run a scan that already has this run's result.

**Non-Goals**

- Callbacks from inside the workflow, and `activeDeadlineSeconds` (see Alternatives).
- Changing the write-back guard: a `'failed'` row stays `'failed'`.
- The RNA-seq poller's 404 handling.

## Decisions

### D1. A table of terminal phases, keyed by run and workflow

- **Why a table, not a column on `cyl_pipeline_run_scans`.** The phase belongs to the workflow, not
  the scan. A column would have to be written to about 25 rows per workflow.
- **Why key by run.** `generateName` names can be reused once Argo deletes a workflow
  (`fix-cyl-pipeline-run-scan-status` addendum 5 estimates about 1% at 500–1,000 submissions).
- **The poller writes only on a change.** It reads the run's stored phases once per cycle and
  calls the RPC only when a live terminal phase differs from the stored one, so a run in a steady
  state costs no writes.
- **The live phase wins over the stored one.** `argo retry` can bring a `Failed` workflow back to
  `Running`. The stored value is only a fallback for a NotFound, and it is overwritten when the
  workflow finishes again.
  - Residual risk: a retried workflow that Argo deletes while the poller is down keeps its old
    stored phase.
- **A failed write changes nothing this cycle.** The poller still uses the live phase. The next
  cycle retries the write while the workflow still exists.

### D2. Verify a 404, and verify ownership

- A 404 means "gone" only when the body is a Kubernetes `Status` whose `reason` is `NotFound` and
  whose `details` name this exact Argo Workflow. Anything else raises `K8sStatusError`:
  - a proxy's HTML page;
  - a missing CRD, which returns a `Status` without the workflow's name;
  - a different reason.
- A live Workflow whose `pipeline-run-id` label names another run, or whose `environment` label
  names another environment, is treated as gone for this run. Prod and staging share the namespace
  and both number runs from 1 (PR #1048 review).
- A phase outside Pending/Running/Succeeded/Failed/Error is a failed lookup, not an outcome.
  Without that check, a reused name would feed another run's phase into this one, and later its
  stored phase too.
- `get_workflow` keeps its behaviour for the RNA-seq poller and the log readers. The checks live in
  a private helper that only `get_workflow_status` uses.
- A wrong **namespace** still produces a verified NotFound for the exact name. D3 limits that case.
- Residual risk: a 404 that can never be verified (an API front end that rewrites error bodies)
  raises on every cycle. That run is isolated and its counts freeze until an operator notices the
  repeated warning. This is preferred to guessing that the workflow is gone.

### D3. Removal: a grace period plus the earliest possible GC

The three conditions are in the spec. The reasons:

- **Consecutive cycles plus a minimum time** filter out a transient NotFound, such as one during an
  API server failover.
- **`newest updated_at + TTL` is a necessary condition, not a sufficient one** (unlike #1038 round
  1's rule).
  - A row's `updated_at` is stamped at dispatch and by write-back while the workflow runs, and Argo deletes a workflow TTL after it _ends_, so deletion
    cannot happen before `updated_at + TTL`.
  - Any NotFound earlier than that is not a garbage collection. A workflow dispatched less than the
    TTL ago is therefore never touched by a namespace or URL mistake. (`created_at` was the first
    choice; the PR #1048 review moved it to `updated_at`, which also covers a dispatch delayed past
    the TTL.)
  - The poller reuses `k8s_client.TTL_SECONDS`, the dispatcher's own resolved value, so the two
    can't drift.
  - If that value is not positive, the guard means nothing, so removal is switched off and the
    poller logs a warning.
- **The count lives in memory, keyed by `(run_id, name)`.**
  - A restart delays a conclusion by one grace period. These conclusions are already hours late.
  - Storing the count would add a write every cycle for every 404'd workflow.
  - Each poller replica counts on its own, so every replica must see the condition for itself.
- **When a count resets.** A run whose check ends early resets every pair it has, because none of
  them was looked up that cycle. A pair absent from a cycle is dropped, which keeps the map bounded.

**Residual risk.** A namespace change that Kubernetes answers with NotFound would, after the grace
period, conclude every still-running run older than the TTL and fail its `'queued'` rows. This is
accepted because:

- the logs would show a NotFound warning for every workflow on every cycle;
- results written later still land as data and show the late-result note (D6);
- the alternative leaves genuinely stuck runs unconcluded forever.

### D4. A removed workflow's phase comes from its rows

- A removed workflow counts as `Succeeded` only if every row is `'written'` or `'reused'`. That
  phase is recorded in `cyl_pipeline_run_workflows` **before** its `'queued'` rows are closed. The
  close-out stamps the rows' `updated_at`, which would restart the TTL guard, and a later failed
  lookup would make the workflow unresolved again (PR #1048 re-review). So a removal has to stick.
- If the close-out fails, `PGRST202` included, the workflow stays unresolved for that cycle.
  Otherwise its leftover `'queued'` rows would read as `Failed` in a run that was about to become
  final.
- **Approximation.** A removed workflow whose producers exited `3` (gate `Succeeded`) but whose
  isolated scans are `'failed'` counts as `Failed`. The run then reads `'partial'`/`'failed'`
  rather than `'complete'`. The counts are exact either way.

### D5. Withhold every terminal conclusion while a workflow is unresolved

Today only `'complete'` is withheld. A `'failed'`/`'partial'` conclusion is written even with an
unresolved sibling, and the backstop closes that sibling's rows. With D6, that conclusion would be
final, and so would its mistakes:

- an old `'partial'` run would flip to `'failed'`;
- a young run's live rows would be failed by a namespace mistake.

The reason not to withhold (addendum 8: a garbage-collected sibling stalls the run forever) no
longer holds, because D3 ends every unresolved state. So rules (2)–(4) all wait, and every row the
terminal backstop closes belongs to a resolved workflow.

The backstop and the running-run close-out both switch to the run-scoped RPC, because a stored
phase now lets them close a workflow whose name may already be reused. `bloomctl` keeps the RPC
that matches on the name alone: it runs inside a live workflow that owns the name.

### D6. Finality: `poller_concluded_at`

- **Why a new column, not `completed_at`.** `_settle_cyl_pipeline_run` stamps `completed_at` for
  `'submitted'` and `'partial'` too, so `completed_at` can't tell "dispatch guessed" from "poller
  confirmed".
- **The guard is in the RPC.** `WHERE status IN ('submitted','running') OR (status = 'partial' AND
poller_concluded_at IS NULL)`. The row lock on that UPDATE makes concurrent terminal writes
  conclude exactly once.
- **The poller doesn't re-select concluded runs.** It selects `id, status, poller_concluded_at` for
  the three statuses and drops concluded `'partial'` rows in code. The PostgREST `or=` filter that
  could do this would need a nested `and(...)`; filtering in code is clearer, and the rows are few.
- **Existing rows start `NULL`, and the poller running today confirms them.** Between PR A and PR B
  the old poller writes its usual rollup to every candidate run within a cycle, so each existing
  poller-written `'partial'` becomes final under today's rules, without D4/D5. Compared with today
  that loses one correction: a `'partial'` that would later have gone back to `'running'` because a
  404'd workflow was in fact alive. `'failed'` and `'complete'` were already final, and that
  workflow's queued rows were already failed for good by the old backstop. Those runs'
  `completed_at`, which the old poller re-stamped every cycle, stays at about the deploy time, so
  it is not a completion time for them. Tasks 3.6 records the affected runs first.
- **`NULL` does not mean open.** A run dispatch alone settled to `'failed'` is never a candidate
  and never gets `poller_concluded_at`.
- **Refused writes are silent.** The RPC keeps its `VOID` signature, so until PR B the old poller
  logs `run X -> partial` for writes the guard refuses. PR B never selects a concluded run, so it never makes a refused write.
- **A retried workflow after conclusion.** `argo retry` on a concluded run's workflow updates its
  stored phase but never the run, whose rows the write-back guard keeps `'failed'`. Re-running
  means a new run.

### D7. Two PRs: database first

The repo's rule (`.claude/commands/database-migration.md`, enforced in warning mode by
`scripts/lint_migration_isolation.py`) is that a PR changing migrations ships alone and its code
follows. It also matters here, because `deploy.yml` starts the new containers before `db push`:

- **New code on the old schema breaks the poller.** The candidate select hits a missing column
  (`42703`), so every sweep fails and the poller reconnects every three cycles.
- **Old code on the new schema is safe.**
  - The column is nullable.
  - The old poller never reads the new table.
  - `update_cyl_pipeline_run_status` keeps its signature. The old poller's repeated `'partial'`
    writes simply stop matching once a run is concluded, which already fixes item 3's flip.

The resulting PRs:

- **PR A:** this proposal, the migration, its rollback, the SQL tests, `database.types.ts` and the ER
  diagram.
- **PR B:** opened once PR A is on staging (and on main before or with PR B in a targeted
  promotion).

Rollback order: revert PR B's code first, then PR A's SQL, as the rollback script's header says.

### D8. Run page: leave late-result rows out of re-runs

- `failedIds` and `unresultedIds` skip rows whose `lateResultNote` is non-null, so the counts and the
  submitted IDs agree.
- If the lookup fails, the note is null and the row is offered.
- A row that turns `'failed'` while the page is open is offered until its batched lookup lands
  (≤ 500 ms).

## Alternatives

- **`activeDeadlineSeconds` at dispatch.** It conflicts with `isolate-cyl-pipeline-environments`'
  "six overrides" requirement. It also needs a dispatch time per workflow, and it kills batches that
  run long.
- **Have the exit gate or an `onExit` step report the end to Bloom.** That needs sleap-roots-pipeline
  and bloomctl changes plus a pin bump, and `check_template_contract.py` can't verify an inline
  step. It is the follow-up to consider if gaps from poller downtime come back.
- **A 404 with dispatch age above TTL + 5 min.** Rejected in #1038 round 2: it was a sufficient
  condition built on the wrong clock.

## Migration

- PR A's migration does the following:
  - creates the table, its RLS, the revokes and grants, and both RPCs;
  - adds `poller_concluded_at` under `lock_timeout`;
  - re-creates `update_cyl_pipeline_run_status` with the same signature;
  - ends with `NOTIFY pgrst, 'reload schema'`.
- After PR B deploys, the poller concludes runs already stuck by #1042 on its own: those runs are
  long past the TTL, so roughly one grace period later.
- Before PR B is promoted, a read-only query on staging and prod lists the runs that will conclude
  (tasks 9.3).
