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
those counts. The exceptions are a failed terminal-rollup reconciliation or recount (below) and
the withheld-`'complete'` rule: when the computed
status is `'complete'` and any of this cycle's workflow lookups returned `None` (404), the call is
skipped entirely this cycle (status and counts both held back, since an unconfirmed workflow could
still resolve to a failure that changes both). Before writing a run's status whenever the computed
status is anything other than `'running'` (and is not withheld by the rule above), the poller SHALL
close out, as `'failed'`, any of that run's `cyl_pipeline_run_scans` rows still `status = 'queued'`
with a non-null `argo_workflow_name` — one `fail_cyl_pipeline_run_scans_without_result` call per
distinct such workflow name — and then re-derive `done_count`/`failed_count` from a fresh read of
that run's scan rows rather than the earlier snapshot (which was taken before this cycle's K8s
lookups and the reconciliation call itself, and can go stale if a scan's write-back genuinely
resolved in that window), since a run whose rollup has already concluded will never be polled again
once its terminal status is written, and this is the only remaining chance to resolve a scan whose
write-back step never ran at all (its own workflow failed before reaching write-back, or the
write-back container never started), or whose write-back step's final attempt still had a
retriable envelope failure (`bloomctl cyl batch-ingest-result` then deliberately makes no
reconciliation call; capability `cyl-batch-ingest-result`). In every cycle that writes no terminal
status — the computed status is `'running'`, no status can be concluded, or `'complete'` is
withheld — the poller SHALL instead close out, the same way, the `'queued'` rows of every *settled*
`argo_workflow_name`: one whose own phase this cycle is `Succeeded`, `Failed`, or `Error`, or one
that returned `None` (`404`) and whose every `'queued'` row was last updated (for a `'queued'` row,
when dispatch stamped its workflow name) longer ago than the Workflow TTL plus a clock-skew
allowance — a `404` that old can only be `ttlStrategy`'s garbage collection, which fires no sooner
than the TTL after the workflow finished. A settled workflow can write no further result. After
such a close-out the poller SHALL re-derive `done_count`/`failed_count` from a fresh read; when the
computed status is `'running'` it SHALL write that status every cycle even if the close-out or the
recount failed (with the snapshot counts, marking the cycle unclean unless the failure is
`PGRST202`), since a `'running'` run stays a candidate regardless and skipping the write would only
freeze its counts. It SHALL log how many rows each reconciliation call closed out. If that reconciliation call itself fails, the run's status
update SHALL be skipped entirely this
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
  this cycle rather than guessed (its `'queued'` rows are still closed out once it is settled)

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

#### Scenario: A terminal rollup reconciles scans write-back deferred after its final retry

- **WHEN** a candidate run's one workflow has finished with its write-back step's final attempt
  having exited non-zero on a retriable envelope failure (so `bloomctl` made no reconciliation call),
  the rollup this cycle concludes a non-`'running'` status, and that envelope's scan and a scan with
  no envelope are both still `'queued'` under the workflow's `argo_workflow_name`
- **THEN** before writing the run's status, the poller calls
  `fail_cyl_pipeline_run_scans_without_result` for that `argo_workflow_name`, both rows become
  `'failed'`, and the run's `failed_count` written this cycle includes both scans

#### Scenario: A still-running workflow's queued rows are not reconciled

- **WHEN** a candidate run's rollup this cycle concludes `'running'`, and the workflow owning a
  `'queued'` row is itself `Pending` or `Running`
- **THEN** the poller does not call `fail_cyl_pipeline_run_scans_without_result` for that
  workflow's `'queued'` rows — they are not stuck, merely not yet resolved

#### Scenario: A terminal workflow's queued rows are reconciled while a sibling still runs

- **WHEN** a candidate run has two workflows, `"wf-a"` whose phase this cycle is `Failed` and
  `"wf-b"` still `Running`, so the rollup concludes `'running'`, and both still have `'queued'` rows
- **THEN** the poller calls `fail_cyl_pipeline_run_scans_without_result` once for `"wf-a"` and never
  for `"wf-b"`, re-derives the counts, and writes `'running'` with a `failed_count` that includes
  `"wf-a"`'s newly closed rows

#### Scenario: A recently dispatched 404'd workflow's queued rows wait

- **WHEN** a workflow with `'queued'` rows returned `None` (`404`) from `get_workflow_status` this
  cycle, and one of those rows was dispatched less than the Workflow TTL ago
- **THEN** the poller makes no reconciliation call for that workflow while the run's rollup is
  `'running'`, unconcluded, or withheld from `'complete'`

#### Scenario: A garbage-collected workflow's rows are closed even when no status can be concluded

- **WHEN** every workflow of a candidate run returns `None` (`404`) — for example because the poller
  could not read them for longer than the Workflow TTL — so the rollup concludes nothing, and their
  `'queued'` rows were all dispatched longer ago than the TTL
- **THEN** the poller calls `fail_cyl_pipeline_run_scans_without_result` for each of those workflows
  and still writes no status for the run
- **AND** the same close-out happens when such a workflow's run is withheld from `'complete'`

#### Scenario: A failed close-out in a running run does not freeze its progress

- **WHEN** a candidate run's rollup concludes `'running'`, one of its workflows is `Failed` with
  `'queued'` rows, and the `fail_cyl_pipeline_run_scans_without_result` call for it (or the recount
  after it) raises an error other than `PGRST202`
- **THEN** the poller still calls `update_cyl_pipeline_run_status` with `'running'` and the snapshot
  counts, marks the cycle unclean, and retries the close-out next cycle

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

#### Scenario: A leftover queued row is reconciled even when a sibling workflow is unresolved this cycle

- **WHEN** a candidate run's rollup concludes `'partial'`/`'failed'` from one or more confirmed-bad
  phases, it has a `'queued'` row under some `argo_workflow_name`, and a *different* workflow in the
  same run returned `None` (404) from `get_workflow_status` this cycle
- **THEN** the poller still calls `fail_cyl_pipeline_run_scans_without_result` for the leftover queued
  row's workflow and still writes the run's status this cycle — reconciliation is not withheld merely
  because some other workflow in the run is unresolved: a 404'd workflow cannot still be silently
  running, so it is treated the same as any other terminal workflow for this purpose, not as
  ambiguous evidence requiring a wait (a prior attempt to withhold in this case was found, during
  review, to let an ordinary TTL-GC'd sibling workflow stall a run's reconciliation and status write
  forever, since such a 404 never resolves)

#### Scenario: A signature-not-found error during the reconciliation call is treated as expected and transient

- **WHEN** the `fail_cyl_pipeline_run_scans_without_result` call raises a PostgREST `APIError` whose
  code is `PGRST202` (the RPC's signature not yet migrated in this environment — the expected,
  transient window between this deploy's app code going live and its migration actually applying)
- **THEN** the poller logs this quietly (not as a warning) and does not mark the cycle unclean, the
  same way `update_cyl_pipeline_run_status`'s own signature-not-found carve-out already behaves — but
  still leaves the run's status update skipped this cycle, same as any other reconciliation failure

#### Scenario: A non-signature-not-found error during the reconciliation call still marks the cycle unclean

- **WHEN** the `fail_cyl_pipeline_run_scans_without_result` call raises any error other than a
  `PGRST202` `APIError` (a different `APIError` code, or any other exception)
- **THEN** the poller marks the cycle unclean, same as the existing "a failed reconciliation call
  leaves the run unsettled" behavior

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
