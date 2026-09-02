## MODIFIED Requirements

### Requirement: A standalone poller periodically reconciles run status against real Argo state

`services/workflows/status_poller.py` SHALL run as a separate, standalone process (its own
docker-compose service, `cyl-status-poller`) with its own poll loop
(`WORKFLOWS_STATUS_POLL_SECONDS`, default `15`), distinct from `dispatch_worker.py`. Each cycle, it
SHALL select every `cyl_pipeline_runs` row whose `status` is `'submitted'`, `'running'`, or `'partial'`
(a `'partial'` run may still have genuinely-dispatched batches whose real Argo outcome hasn't been
checked — see the `'partial'`-inclusion scenario below), and for each such run: collect the distinct
`argo_workflow_name` values and the full `status` column from that run's `cyl_pipeline_run_scans`
rows (the same fetch already used to build effective phases, extended to also compute counts), call
`get_workflow_status` for each distinct workflow name, compute the run's rollup status (see the
rollup requirement below), compute `done_count` (the number of that run's scan rows with `status IN
('written', 'reused')`) and `failed_count` (the number with `status = 'failed'`), and — whenever the
effective-phase list was non-empty (i.e. rule (0) of the rollup did not withhold a conclusion) — call
`update_cyl_pipeline_run_status` with the rollup status and these two counts, **every cycle a
candidate run has scan rows to check, regardless of whether the computed status differs from the
run's already-known status** — a still-`'running'` run's `done_count`/`failed_count` can advance
between cycles even while its overall status does not, so an unchanged-status shortcut would freeze
those counts. The sole remaining exception is the withheld-`'complete'` rule: when the computed
status is `'complete'` and any of this cycle's workflow lookups returned `None` (404), the call is
skipped entirely this cycle (status and counts both held back, since an unconfirmed workflow could
still resolve to a failure that changes both). Before writing a run's status whenever the computed
status is anything other than `'running'` (and is not withheld by the rule above), the poller SHALL
close out, as `'failed'`, any of that run's `cyl_pipeline_run_scans` rows still `status = 'queued'`
with a non-null `argo_workflow_name` — one `fail_cyl_pipeline_run_scans_without_result` call per
distinct such workflow name, folding the closed-out rows into this cycle's `failed_count` — since a
run whose rollup has already concluded will never be polled again once its terminal status is
written, and this is the only remaining chance to resolve a scan whose write-back step never ran at
all (its own workflow failed before reaching write-back, or the write-back container never started).
If that reconciliation call itself fails, the run's status update SHALL be skipped entirely this
cycle (the run's `cyl_pipeline_runs.status` left untouched, so it remains a candidate and is retried
next cycle), matching the isolation the rule below already gives every other per-run failure. It SHALL isolate a failure fetching or updating any one
run (a K8s error, a DB-read error, or a failed write) to that run alone, never aborting the rest of the
cycle's candidates, and SHALL isolate a failure fetching the candidate list itself to that cycle alone
(retrying next cycle, not crashing). Because this per-run/per-cycle isolation means a single sweep
essentially never lets an exception propagate out of it, the poller's outer loop SHALL track whether
each cycle completed cleanly (no isolated errors) and, after `3` consecutive unclean cycles,
proactively obtain a fresh Supabase client rather than continuing to reuse a client whose session may
have genuinely died — since a caught-and-isolated error no longer reaches the outer loop's own
reconnect-on-exception handling the way it did before per-run isolation existed. It SHALL handle
`SIGTERM`/`SIGINT` gracefully (finish the in-flight sweep before exiting, do not start a new one), and
SHALL retry its startup Supabase connection with backoff rather than crash on a transient outage,
matching `dispatch_worker.py`'s established conventions for both.

#### Scenario: A run with one workflow still Running stays running

- **WHEN** a run has a single batch whose Argo workflow's real phase is `Running`
- **THEN** the poller computes `'running'` and calls `update_cyl_pipeline_run_status` with `'running'`

#### Scenario: A run whose only workflow Succeeded becomes complete

- **WHEN** a run has a single batch whose Argo workflow's real phase is `Succeeded`
- **THEN** the poller computes `'complete'`

#### Scenario: A run with no distinct workflows to check is left alone

- **WHEN** a candidate run (status `'submitted'`, `'running'`, or `'partial'`) has no
  `cyl_pipeline_run_scans` rows with a non-null `argo_workflow_name` (should not happen given Phase 2's
  own invariants, but the poller must not error if it does)
- **THEN** the poller does not call `update_cyl_pipeline_run_status` for that run this cycle

#### Scenario: A `'partial'` run's genuinely-dispatched batches are checked, not skipped

- **WHEN** a candidate run's current `status` is `'partial'` (Phase 2's dispatch-level outcome — some
  scans failed to dispatch, some succeeded) and it has at least one `cyl_pipeline_run_scans` row with a
  non-null `argo_workflow_name`
- **THEN** the poller calls `get_workflow_status` for that workflow the same as it would for a
  `'submitted'`/`'running'` run — it is not excluded from checking merely because its current status is
  already `'partial'`

#### Scenario: A workflow that 404s is skipped, not treated as terminal

- **WHEN** one of a run's workflows returns `None` from `get_workflow_status` (a `404`)
- **THEN** that workflow does not contribute a phase to the rollup computation this cycle
- **AND** if it was the only workflow the run had left to check, the run's status is left unchanged
  this cycle rather than guessed

#### Scenario: A 404 among otherwise-Succeeded siblings withholds a `'complete'` conclusion, not writes one

- **WHEN** a run has two batches, one whose workflow returns `Succeeded` this cycle and one whose
  workflow returns `None` (404 — TTL-expired before ever being observed as terminal)
- **THEN** the poller does NOT call `update_cyl_pipeline_run_status` with `'complete'` for that run
  this cycle, even though the *observed* phases alone would satisfy rule (2) — a workflow whose real
  outcome was never confirmed must not be silently treated as if it had succeeded, and `done_count`/
  `failed_count` are likewise not written this cycle

#### Scenario: A failure checking one run does not abort the sweep for other runs

- **WHEN** a sweep cycle has two or more candidate runs, and checking the first run's workflow(s) or
  reading its `cyl_pipeline_run_scans` rows raises any exception (a K8s error, a DB-read error), or the
  `update_cyl_pipeline_run_status` call for that run itself fails
- **THEN** the sweep still checks and, where warranted, updates every other candidate run in the same
  cycle — a problem isolated to one run's K8s lookups, DB read, or DB write must not silently skip the
  rest of that cycle's candidates

#### Scenario: A failure fetching the candidate list itself does not crash the poller

- **WHEN** the query that fetches this cycle's candidate runs (`_fetch_candidate_runs`) itself raises
- **THEN** the current sweep cycle ends without checking any run (equivalent to finding zero
  candidates), and the next scheduled cycle retries — the process does not crash or exit

#### Scenario: A still-`'running'` run's counts advance even though its status does not change

- **WHEN** a candidate run's already-known `status` is `'running'`, this cycle's rollup also computes
  `'running'` (no status transition), but one additional scan has moved to `'written'` since the last
  sweep
- **THEN** the poller still calls `update_cyl_pipeline_run_status` this cycle, with `p_status =
  'running'` and the newly higher `done_count` — this is a deliberate change from prior behavior,
  which skipped the call entirely when the computed status matched a known `'running'` status; that
  skip is removed because it would otherwise freeze `done_count`/`failed_count` at whatever they were
  on the run's first `'running'` cycle

#### Scenario: A dispatch-settled `'partial'` run's first real confirmation still writes, even though the computed value matches the known string

- **WHEN** a candidate run's already-known `status` is `'partial'` (Phase 2's dispatch-time settle — this
  poller has not yet confirmed any real Argo outcome for it) and this cycle's rollup computes `'partial'`
  as the run's real, final pipeline-level outcome
- **THEN** the poller DOES call `update_cyl_pipeline_run_status` with `'partial'` and this run's current
  `done_count`/`failed_count` — a `'partial'` candidate's known status cannot be trusted to mean
  "already confirmed by this poller," unlike `'running'`, since Phase 2's own dispatch-settle can also
  produce `'partial'` as a pre-poll guess (found `/review-pr` round 3, correcting a round-2 regression
  that silently discarded exactly this write)

#### Scenario: A terminal rollup reconciles a scan whose write-back step never ran

- **WHEN** a candidate run's rollup this cycle concludes `'failed'` (its one workflow's real Argo
  phase resolved to a terminal, non-`Succeeded` outcome), and one of this run's
  `cyl_pipeline_run_scans` rows is still `status = 'queued'` under that workflow's
  `argo_workflow_name` (write-back never ran for that scan, e.g. the workflow failed before reaching
  the write-back step)
- **THEN** before writing the run's status, the poller calls
  `fail_cyl_pipeline_run_scans_without_result` for that `argo_workflow_name`, and the run's
  `failed_count` written this cycle includes that scan

#### Scenario: A still-running workflow's queued rows are not reconciled

- **WHEN** a candidate run's rollup this cycle concludes `'running'`
- **THEN** the poller does not call `fail_cyl_pipeline_run_scans_without_result` for any of that
  run's `'queued'` rows — they are not stuck, merely not yet resolved

#### Scenario: A failed reconciliation call leaves the run unsettled for the next cycle

- **WHEN** a candidate run's rollup concludes a non-`'running'` status, it has a `'queued'` row under
  some `argo_workflow_name`, and the `fail_cyl_pipeline_run_scans_without_result` call for that
  workflow name raises
- **THEN** the poller does not call `update_cyl_pipeline_run_status` for that run this cycle (the run
  remains a polling candidate, unchanged), and the cycle continues checking the remaining candidates

#### Scenario: A run with no leftover queued rows is unaffected

- **WHEN** a candidate run's rollup concludes a non-`'running'` status and every one of its scan rows
  already has a status other than `'queued'`
- **THEN** the poller makes no `fail_cyl_pipeline_run_scans_without_result` call for that run

#### Scenario: Three consecutive unclean cycles trigger a proactive reconnect

- **WHEN** three sweep cycles in a row each have at least one isolated error (a K8s error, a DB-read
  error, or a failed write for some candidate run, or a failure fetching the candidate list itself)
- **THEN** the poller obtains a fresh Supabase client before starting the next cycle, rather than
  continuing to reuse the same client indefinitely

#### Scenario: A single isolated error does not trigger a reconnect

- **WHEN** one sweep cycle has an isolated error but the immediately following cycle completes cleanly
- **THEN** the poller does not reconnect — the consecutive-unclean-cycle count resets on the clean cycle

#### Scenario: SIGTERM lets the in-flight sweep finish

- **WHEN** `SIGTERM` is received while the poller is mid-sweep (partway through checking one run's
  workflows)
- **THEN** the current run's checks and any resulting `update_cyl_pipeline_run_status` call complete
  before the loop exits
- **AND** no new sweep cycle starts afterward

#### Scenario: A transient startup Supabase outage does not crash-loop the process

- **WHEN** the Supabase connection fails on process startup
- **THEN** the poller retries with backoff (matching `dispatch_worker.py`'s `_connect_with_retry`
  convention) instead of raising an uncaught exception

### Requirement: `update_cyl_pipeline_run_status` writes the rollup result under least privilege

The database SHALL provide `update_cyl_pipeline_run_status(p_run_id bigint, p_status text, p_done_count
integer DEFAULT NULL, p_failed_count integer DEFAULT NULL) RETURNS void`, `SECURITY DEFINER`,
validating `p_status` is one of `'running'|'complete'|'failed'|'partial'` (raising an error otherwise),
and updating `cyl_pipeline_runs` only when the row's current `status` is `'submitted'`, `'running'`, or
`'partial'` — a run already `'queued'` (never dispatched) or already `'complete'`/`'failed'` is left
untouched. The update SHALL always set `status = p_status`, and SHALL set `done_count =
COALESCE(p_done_count, done_count)` and `failed_count = COALESCE(p_failed_count, failed_count)` —
passing `NULL` for either (the default) leaves that column unchanged, so existing callers that never
supply them continue to work exactly as before. On a transition into a terminal status
(`'complete'`/`'failed'`/`'partial'`), `completed_at` SHALL be set to `now()` **unconditionally** —
every call that reaches this branch advances it, not only the first (Phase 2's own dispatch-settle
write already sets `completed_at` for the common `'submitted'` case before this function is ever
called, so a guard that only fires when `completed_at IS NULL` would never actually run in production;
see `design.md`'s `completed_at` decision for why the previous `IS NULL` guard was wrong, and why an
always-fresh timestamp is preferred over comparing against the run's previous stored `status` value).
`EXECUTE` SHALL be revoked from `PUBLIC`, `anon`, and `authenticated`, and granted only to
`bloom_workflows`, the same triple-revoke/single-grant pattern every other `SECURITY DEFINER` wrapper
in this program uses.

#### Scenario: bloom_workflows can update a submitted run to running

- **WHEN** a session with role `bloom_workflows` calls `update_cyl_pipeline_run_status` with a
  `p_run_id` currently `'submitted'` and `p_status = 'running'`
- **THEN** the call succeeds and the run's `status` becomes `'running'`
- **AND** `completed_at` remains unchanged (`'running'` is not a terminal status)

#### Scenario: Transitioning into a terminal status sets completed_at

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status = 'complete'` for a run whose
  `completed_at` is currently `NULL`
- **THEN** `completed_at` is set to the current time

#### Scenario: A stale, dispatch-time completed_at is overwritten with the real completion time

- **WHEN** `update_cyl_pipeline_run_status` is called with a terminal `p_status` for a run whose
  `completed_at` is already set to an earlier timestamp (e.g. stamped by Phase 2's own dispatch-settle
  write when the run first became `'submitted'`, long before the pipeline itself finished)
- **THEN** `completed_at` is overwritten to the current time, reflecting when this real conclusion was
  actually reached — not left frozen at the earlier, dispatch-time value

#### Scenario: A `'partial'` run can be re-confirmed, advancing completed_at again

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status = 'partial'` for a run whose
  current `status` is already `'partial'`
- **THEN** the call succeeds (the guard's allowed source states include `'partial'`) and `completed_at`
  is advanced to the current time again — an accepted, documented consequence of `'partial'` runs
  remaining pollable (see `design.md`), not a bug

#### Scenario: A run already complete or failed is left untouched

- **WHEN** `update_cyl_pipeline_run_status` is called for a run whose current `status` is already
  `'complete'` or `'failed'`
- **THEN** the call completes without error
- **AND** the run's `status`, `done_count`, `failed_count`, and `completed_at` are unchanged

#### Scenario: A run still queued is left untouched

- **WHEN** `update_cyl_pipeline_run_status` is called for a run whose current `status` is `'queued'`
  (never dispatched)
- **THEN** the call completes without error
- **AND** the run's `status` is unchanged — this function has nothing to say about a run Phase 2 never
  submitted

This path is not reachable via the poller in production — its own candidate-selection query only ever
considers `'submitted'`/`'running'`/`'partial'` runs (see the poller requirement above), so a `'queued'`
run is never passed to this function by anything except a direct call. This scenario exists to pin the
function's own defense-in-depth guard, exercised by calling the RPC directly (as the integration test
does), not by adding a redundant "skip if queued" check inside the poller itself.

#### Scenario: A nonexistent run id is a harmless no-op

- **WHEN** `update_cyl_pipeline_run_status` is called with a `p_run_id` that matches no row
- **THEN** the call completes without error
- **AND** no row anywhere is modified

#### Scenario: An invalid status value is rejected

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status` not in
  `'running'|'complete'|'failed'|'partial'`
- **THEN** the call raises an error and no row is modified

#### Scenario: EXECUTE is denied to anon, authenticated, PUBLIC, and every session role except bloom_workflows

- **WHEN** `has_function_privilege` is checked for `anon`, `authenticated`, the implicit `PUBLIC`
  grantee, and `bloom_user`/`bloom_writer`/`bloom_admin` against this function's signature
- **THEN** each reports `EXECUTE` as `false`
- **AND** the same check for `bloom_workflows` reports `true`

#### Scenario: Supplying done_count and failed_count updates both columns

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status = 'running'`, `p_done_count = 5`,
  and `p_failed_count = 1` for a run currently `'running'`
- **THEN** the run's `done_count` becomes `5` and `failed_count` becomes `1`, alongside the unchanged
  `status`

#### Scenario: Omitting done_count and failed_count leaves them unchanged

- **WHEN** `update_cyl_pipeline_run_status` is called with only `p_run_id` and `p_status` (the
  pre-existing two-argument call shape, e.g. from any caller not yet updated to pass counts)
- **THEN** the call succeeds and `done_count`/`failed_count` are left exactly as they were before the
  call
