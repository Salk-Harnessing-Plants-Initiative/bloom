## MODIFIED Requirements

### Requirement: `k8s_client.get_workflow_status` reads a single Workflow's real phase

`services/workflows/k8s_client.py` SHALL provide `get_workflow_status(name: str, run_id: int | None = None) -> str | None`,
issuing `GET {API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows/{name}` with the same
bearer token and TLS configuration `submit_workflow` already uses, and returning the value of
`.status.phase` from the response body on a `2xx` response. It SHALL return `None` (not raise) only
when the response is a **verified NotFound**: status `404` with a JSON object body whose `kind` is
`"Status"`, whose `reason` is `"NotFound"`, and whose `details` has `name` equal to the requested
workflow name, `kind` equal to `"workflows"` and `group` equal to `"argoproj.io"` — an expected
condition (the Workflow already self-deleted via `ttlStrategy`, or was deleted by an operator), not a
failure. Any other `404` (a non-JSON body such as a proxy's page, a `Status` with another reason, or
`details` naming anything other than this workflow, as a missing CRD or wrong API path produces) SHALL
be logged server-side with its body and raise `K8sStatusError`, since it is not evidence that the
workflow is gone. When `run_id` is given and the Workflow found carries a `pipeline-run-id` label
whose value is not `str(run_id)`, it SHALL also return `None`: a garbage-collected workflow's
generated name can be reused by a later dispatch, and that Workflow is not this run's (a Workflow with
no such label is treated as this run's). `get_workflow`, which other pollers call, SHALL keep
returning `None` on any `404`.
It SHALL raise a new `K8sStatusError` for any other non-2xx
response or network-level failure, constructed with a fixed, generic message — never the raw response
body, exception text, or API server URL — matching `K8sSubmissionError`'s existing sanitization
convention, since this error's message may end up in a user-facing field. `_validate_config()` SHALL be
called first, raising `K8sConfigError` before any network call if credentials are missing, identical to
`submit_workflow`.

#### Scenario: A Succeeded workflow's phase is read

- **WHEN** `get_workflow_status` is called for a workflow name whose real status is `Succeeded`
- **THEN** it returns the string `"Succeeded"`

#### Scenario: A missing workflow returns None, not an error

- **WHEN** `get_workflow_status("wf-a")` gets a `404` whose body is a Kubernetes `Status` with
  `reason: "NotFound"` and `details: {name: "wf-a", kind: "workflows", group: "argoproj.io"}`
- **THEN** it returns `None`
- **AND** no exception is raised

#### Scenario: A 404 that does not name the workflow raises

- **WHEN** `get_workflow_status("wf-a")` gets a `404` whose body is an HTML page, or a `Status` whose
  `details.name` is absent or not `"wf-a"`, or whose `reason` is not `"NotFound"`
- **THEN** it raises `K8sStatusError` with the fixed, generic message
- **AND** `get_workflow("wf-a")` given the same response still returns `None`

#### Scenario: A reused name belonging to another run reads as gone for this run

- **WHEN** `get_workflow_status("wf-a", run_id=7)` finds a Workflow whose
  `metadata.labels["pipeline-run-id"]` is `"9"`
- **THEN** it returns `None`
- **AND** the same Workflow with label `"7"`, or with no `pipeline-run-id` label, returns its phase

#### Scenario: A non-404 failure raises a sanitized error

- **WHEN** the K8s API returns a `5xx` response, or the request fails at the network level
- **THEN** `get_workflow_status` raises `K8sStatusError`
- **AND** `str(K8sStatusError)` is a fixed, generic message, never the raw response body or exception
  text

#### Scenario: Missing credentials raise before any network call

- **WHEN** `get_workflow_status` is called while `WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL` are not
  all set
- **THEN** it raises `K8sConfigError` naming the missing variable(s)
- **AND** no network request is attempted

### Requirement: A standalone poller periodically reconciles run status against real Argo state

`services/workflows/status_poller.py` SHALL run as a separate, standalone process (its own
docker-compose service, `cyl-status-poller`) with its own poll loop
(`WORKFLOWS_STATUS_POLL_SECONDS`, default `15`), distinct from `dispatch_worker.py`. Each cycle, it
SHALL select every `cyl_pipeline_runs` row whose `status` is `'submitted'` or `'running'`, or whose
`status` is `'partial'` with `poller_concluded_at IS NULL` (a dispatch-settled `'partial'` run may
still have genuinely-dispatched batches whose real Argo outcome hasn't been checked — see the
`'partial'`-inclusion scenario below; a run this poller already concluded is final and never selected
again), and for each such run: collect the distinct
`argo_workflow_name` values and the full `status` and `created_at` columns from that run's
`cyl_pipeline_run_scans` rows (the same fetch already used to build effective phases, extended to
also compute counts), read that run's stored phases from `cyl_pipeline_run_workflows`, call
`get_workflow_status(name, run_id)` for each distinct workflow name, and resolve each workflow's
**effective phase**:

1. the live phase when the lookup returned one, recording it through
   `record_cyl_pipeline_workflow_phase` when it is `Succeeded`, `Failed` or `Error` and differs from
   the stored phase (a failed record call marks the cycle unclean, except `PGRST202`, and does not
   change the effective phase);
2. otherwise (a verified NotFound for this run), the stored phase if one exists;
3. otherwise, if the workflow is **removed** (below) and its `'queued'` rows were closed out this
   cycle, `Succeeded` when every one of its rows is then `'written'` or `'reused'` and `Failed`
   otherwise;
4. otherwise the workflow is **unresolved** this cycle and contributes no phase.

A workflow is **removed** when it has no stored phase and all three hold: the poller's lookups of it
for this run have returned a verified NotFound on at least `3` consecutive cycles in which it was
looked up; at least `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` (default `600`; a malformed or non-positive
value falls back to the default with a warning) have passed since the first of those; and at least
`k8s_client.TTL_SECONDS` (`WORKFLOWS_K8S_TTL_SECONDS`, the value the dispatcher stamps as
`ttlStrategy.secondsAfterCompletion`) have passed since the newest `created_at` among its rows — a
row is created before its workflow is dispatched, so garbage collection cannot have happened earlier.
When `k8s_client.TTL_SECONDS` is not positive, no workflow is ever removed and the poller logs a
warning at startup. The consecutive count is kept in the poller's memory per `(run_id,
argo_workflow_name)`: any lookup of that pair that is not a verified NotFound resets it, an
exception that ends a run's check resets every pair of that run, and a pair not looked up in a cycle
is dropped. To close out a removed workflow's `'queued'` rows the poller SHALL call
`close_cyl_pipeline_run_workflow_scans` with the message "the workflow was removed before Bloom saw
it finish; check whether a result file exists before re-running prediction" and derive the phase from
a fresh read of its rows; if that call fails (including `PGRST202`), the workflow stays unresolved
this cycle and the cycle is unclean unless the failure is `PGRST202`.

It SHALL then compute the run's rollup status (see the
rollup requirement below), compute `done_count` (the number of that run's scan rows with `status IN
('written', 'reused')`) and `failed_count` (the number with `status = 'failed'`), and — whenever the
effective-phase list was non-empty (i.e. rule (0) of the rollup did not withhold a conclusion) — call
`update_cyl_pipeline_run_status` with the rollup status and these two counts, **every cycle a
candidate run has scan rows to check, regardless of whether the computed status differs from the
run's already-known status** — a still-`'running'` run's `done_count`/`failed_count` can advance
between cycles even while its overall status does not, so an unchanged-status shortcut would freeze
those counts. The exceptions are a failed terminal-rollup reconciliation or recount (below) and
the withheld-conclusion rule: when the computed status is anything other than `'running'` and any of
the run's workflows is unresolved this cycle, the call is skipped entirely this cycle (status and
counts both held back, and no row closed out, since an unconfirmed workflow could still be running
or resolve to an outcome that changes both); the removal rule above bounds how long that lasts.
Before writing a run's status whenever the computed
status is anything other than `'running'` (and is not withheld by the rule above), the poller SHALL
close out, as `'failed'`, any of that run's `cyl_pipeline_run_scans` rows still `status = 'queued'`
with a non-null `argo_workflow_name` — one `close_cyl_pipeline_run_workflow_scans` call per
distinct such workflow name, scoped to this run, with the existing write-back backstop message — and
then re-derive `done_count`/`failed_count` from a fresh read of
that run's scan rows rather than the earlier snapshot (which was taken before this cycle's K8s
lookups and the reconciliation call itself, and can go stale if a scan's write-back genuinely
resolved in that window), since a run whose rollup has already concluded will never be polled again
once its terminal status is written, and this is the only remaining chance to resolve a scan whose
write-back step never ran at all (its own workflow failed before reaching write-back, or the
write-back container never started), or whose write-back step's final attempt still had a
retriable envelope failure (`bloomctl cyl batch-ingest-result` then deliberately makes no
reconciliation call; capability `cyl-batch-ingest-result`). While the computed status is
`'running'`, the poller SHALL also close out, the same way, the `'queued'` rows of every
`argo_workflow_name` whose effective phase this cycle is `Succeeded`, `Failed`, or `Error` from a
live or stored phase — never an unresolved workflow's, since a NotFound inside the grace period can
also come from a misconfigured namespace while the workflow still runs; such rows wait for the
removal rule — and
SHALL then re-derive `done_count`/`failed_count` from a fresh read. It SHALL write the `'running'`
status every such cycle even if that close-out or recount failed (with the snapshot counts,
marking the cycle unclean unless the failure is `PGRST202`), since a `'running'` run stays a
candidate regardless and skipping the write would only freeze its counts. It SHALL log how many
rows each reconciliation call closed out. If that reconciliation call itself fails, the run's status
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
  scans failed to dispatch, some succeeded) with `poller_concluded_at IS NULL`, and it has at least
  one `cyl_pipeline_run_scans` row with a non-null `argo_workflow_name`
- **THEN** the poller calls `get_workflow_status` for that workflow the same as it would for a
  `'submitted'`/`'running'` run — it is not excluded from checking merely because its current status is
  already `'partial'`

#### Scenario: An unresolved workflow is skipped, not treated as terminal

- **WHEN** one of a run's workflows returns `None` from `get_workflow_status` (a verified NotFound),
  has no stored phase, and is not yet removed
- **THEN** that workflow does not contribute a phase to the rollup computation this cycle
- **AND** if it was the only workflow the run had left to check, the run's status is left unchanged
  this cycle rather than guessed

#### Scenario: An unresolved workflow among otherwise-Succeeded siblings withholds a `'complete'` conclusion, not writes one

- **WHEN** a run has two batches, one whose workflow returns `Succeeded` this cycle and one whose
  workflow is unresolved (a verified NotFound with no stored phase, inside its grace period)
- **THEN** the poller does NOT call `update_cyl_pipeline_run_status` with `'complete'` for that run
  this cycle, even though the *observed* phases alone would satisfy rule (2) — a workflow whose real
  outcome was never confirmed must not be silently treated as if it had succeeded, and `done_count`/
  `failed_count` are likewise not written this cycle

#### Scenario: A terminal phase is recorded once and outlives garbage collection

- **WHEN** a run has workflows `"wf-a"` and `"wf-b"`; on one cycle `"wf-a"` returns `Succeeded`
  (nothing stored yet) and `"wf-b"` returns `Running`; on a later cycle `"wf-a"` returns a verified
  NotFound and `"wf-b"` returns `Succeeded`
- **THEN** the first cycle calls `record_cyl_pipeline_workflow_phase(run, "wf-a", "Succeeded")`
  exactly once and writes `'running'`
- **AND** the later cycle uses the stored `Succeeded` for `"wf-a"`, records `"wf-b"`, and writes
  `'complete'` with fresh counts — `'complete'` is not withheld
- **AND** a cycle in which `"wf-a"` returns `Succeeded` again and the stored phase is already
  `Succeeded` makes no record call

#### Scenario: A live phase wins over a stored one

- **WHEN** `"wf-a"` has stored phase `Failed` and its lookup this cycle returns `Running` (an operator
  retried it)
- **THEN** its effective phase is `Running`, the rollup is `'running'`, and no record call is made
  until it is terminal again

#### Scenario: A workflow gone before the poller saw it finish is removed after the grace period

- **WHEN** a run's only workflow `"wf-a"` has no stored phase, its rows were created more than
  `WORKFLOWS_K8S_TTL_SECONDS` ago, two rows are `'written'` and one is `'queued'`, and its lookups
  return a verified NotFound on 3 consecutive cycles spanning at least
  `WORKFLOWS_NOT_FOUND_GRACE_SECONDS`
- **THEN** on the cycle that meets both conditions the poller calls
  `close_cyl_pipeline_run_workflow_scans(run, "wf-a", <the removed-workflow message>)`, counts
  `"wf-a"` as `Failed`, and writes `'failed'` with `done_count = 2` and `failed_count = 1`
- **AND** on every earlier cycle it makes no reconciliation call and no status write for that run

#### Scenario: A removed workflow whose rows were all written counts as Succeeded

- **WHEN** a run has `"wf-a"` with stored phase `Succeeded` and `"wf-b"` removed with every row
  `'written'`
- **THEN** the poller makes no reconciliation call for `"wf-b"` and writes `'complete'`

#### Scenario: A removed workflow whose close-out fails stays unresolved

- **WHEN** `"wf-a"` meets every removal condition but `close_cyl_pipeline_run_workflow_scans` for it
  raises (any error, `PGRST202` included)
- **THEN** `"wf-a"` is unresolved this cycle, so the run's terminal conclusion is withheld and no
  status is written
- **AND** the cycle is unclean unless the error was `PGRST202`, and the next cycle tries again

#### Scenario: A NotFound too soon after the rows were created never removes a workflow

- **WHEN** `"wf-a"` has no stored phase and returns a verified NotFound on every cycle for longer
  than the grace period, but its newest row was created less than `WORKFLOWS_K8S_TTL_SECONDS` ago
- **THEN** it stays unresolved: no reconciliation call is made for it and every terminal conclusion for the run stays withheld

#### Scenario: Any other lookup result resets the NotFound count

- **WHEN** `"wf-a"` returns a verified NotFound on 2 cycles, then `get_workflow_status` raises
  `K8sStatusError` for it on one cycle, then it returns a verified NotFound again
- **THEN** the count restarts at 1 and the grace period restarts from that later NotFound

#### Scenario: A failed phase record does not change the cycle's conclusion

- **WHEN** `record_cyl_pipeline_workflow_phase` raises an error other than `PGRST202` for a workflow
  whose live phase is `Succeeded`
- **THEN** the poller still uses `Succeeded` for that workflow this cycle, writes the run's status,
  and marks the cycle unclean

#### Scenario: A run the poller concluded is not selected again

- **WHEN** a run has `status = 'partial'` and a non-null `poller_concluded_at`
- **THEN** `_fetch_candidate_runs` does not return it and the poller looks up none of its workflows

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
  `close_cyl_pipeline_run_workflow_scans` for that `argo_workflow_name`, and the run's
  `failed_count` written this cycle includes that scan

#### Scenario: A terminal rollup reconciles scans write-back deferred after its final retry

- **WHEN** a candidate run's one workflow has finished with its write-back step's final attempt
  having exited non-zero on a retriable envelope failure (so `bloomctl` made no reconciliation call),
  the rollup this cycle concludes a non-`'running'` status, and that envelope's scan and a scan with
  no envelope are both still `'queued'` under the workflow's `argo_workflow_name`
- **THEN** before writing the run's status, the poller calls
  `close_cyl_pipeline_run_workflow_scans` for that `argo_workflow_name`, both rows become
  `'failed'`, and the run's `failed_count` written this cycle includes both scans

#### Scenario: A still-running workflow's queued rows are not reconciled

- **WHEN** a candidate run's rollup this cycle concludes `'running'`, and the workflow owning a
  `'queued'` row is itself `Pending` or `Running`
- **THEN** the poller does not call `close_cyl_pipeline_run_workflow_scans` for that
  workflow's `'queued'` rows — they are not stuck, merely not yet resolved

#### Scenario: A terminal workflow's queued rows are reconciled while a sibling still runs

- **WHEN** a candidate run has two workflows, `"wf-a"` whose phase this cycle is `Failed` and
  `"wf-b"` still `Running`, so the rollup concludes `'running'`, and both still have `'queued'` rows
- **THEN** the poller calls `close_cyl_pipeline_run_workflow_scans` once for `"wf-a"` and never
  for `"wf-b"`, re-derives the counts, and writes `'running'` with a `failed_count` that includes
  `"wf-a"`'s newly closed rows

#### Scenario: An unresolved workflow's queued rows wait while the run is running

- **WHEN** a candidate run's rollup concludes `'running'`, and a workflow with `'queued'` rows is
  unresolved this cycle (a verified NotFound with no stored phase, not yet removed)
- **THEN** the poller makes no reconciliation call for that workflow this cycle; its rows are
  closed out once it is removed

#### Scenario: A stored-phase workflow's queued rows are closed while the run is running

- **WHEN** a candidate run's rollup concludes `'running'` because `"wf-b"` is `Running`, and
  `"wf-a"`, which has `'queued'` rows, returns a verified NotFound with stored phase `Failed`
- **THEN** the poller calls `close_cyl_pipeline_run_workflow_scans` for `"wf-a"` with the
  existing write-back message, and writes `'running'` with fresh counts

#### Scenario: A failed close-out in a running run does not freeze its progress

- **WHEN** a candidate run's rollup concludes `'running'`, one of its workflows is `Failed` with
  `'queued'` rows, and the `close_cyl_pipeline_run_workflow_scans` call for it (or the recount
  after it) raises an error other than `PGRST202`
- **THEN** the poller still calls `update_cyl_pipeline_run_status` with `'running'` and the snapshot
  counts, marks the cycle unclean, and retries the close-out next cycle

#### Scenario: A failed reconciliation call leaves the run unsettled for the next cycle

- **WHEN** a candidate run's rollup concludes a non-`'running'` status, it has a `'queued'` row under
  some `argo_workflow_name`, and the `close_cyl_pipeline_run_workflow_scans` call for that
  workflow name raises
- **THEN** the poller does not call `update_cyl_pipeline_run_status` for that run this cycle (the run
  remains a polling candidate, unchanged), and the cycle continues checking the remaining candidates

#### Scenario: A run with no leftover queued rows is unaffected

- **WHEN** a candidate run's rollup concludes a non-`'running'` status and every one of its scan rows
  already has a status other than `'queued'`
- **THEN** the poller makes no `close_cyl_pipeline_run_workflow_scans` call for that run

#### Scenario: A terminal conclusion waits while a sibling workflow is unresolved

- **WHEN** a candidate run's rollup would conclude `'partial'`/`'failed'` from one or more
  confirmed-bad phases, it has a `'queued'` row under a terminal workflow, and a *different* workflow
  in the same run is unresolved this cycle
- **THEN** the poller makes no `close_cyl_pipeline_run_workflow_scans` call for the unresolved
  workflow and no `update_cyl_pipeline_run_status` call for the run this cycle — a NotFound inside the
  grace period can come from a misconfigured namespace while that workflow still runs, and a written
  terminal status is final
- **AND** once the unresolved workflow is removed (or a live or stored phase resolves it), the run
  concludes in that cycle with every `'queued'` row closed out

#### Scenario: A poller-concluded partial from before this change is not turned failed by an unresolved sibling

- **WHEN** a run is `'partial'` with `poller_concluded_at IS NULL` (concluded by the poller before
  `poller_concluded_at` existed), its dispatch-failed scan gives an effective `'Failed'`, and its one
  workflow `"wf-a"` has no stored phase and returns a verified NotFound
- **THEN** the poller writes nothing for the run until `"wf-a"` is removed
- **AND** once removed, `"wf-a"`'s phase is derived from its rows, so a run whose `"wf-a"` rows are
  all `'written'` is written `'partial'`, not `'failed'`

#### Scenario: A signature-not-found error during the reconciliation call is treated as expected and transient

- **WHEN** the `close_cyl_pipeline_run_workflow_scans` call raises a PostgREST `APIError` whose
  code is `PGRST202` (the RPC's signature not yet migrated in this environment — the expected,
  transient window between this deploy's app code going live and its migration actually applying)
- **THEN** the poller logs this quietly (not as a warning) and does not mark the cycle unclean, the
  same way `update_cyl_pipeline_run_status`'s own signature-not-found carve-out already behaves — but
  still leaves the run's status update skipped this cycle, same as any other reconciliation failure

#### Scenario: A non-signature-not-found error during the reconciliation call still marks the cycle unclean

- **WHEN** the `close_cyl_pipeline_run_workflow_scans` call raises any error other than a
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

### Requirement: Rollup rule for aggregating per-workflow phases into one run status

The rollup SHALL compute a run's status, in order, from the *effective phase* of every one of its
scans: `'Failed'` for a scan whose dispatch itself failed (`cyl_pipeline_run_scans.status = 'failed'`
AND `argo_workflow_name IS NULL`), otherwise the real Argo phase of that scan's batch's workflow
(its effective phase as the poller requirement above defines it — live, stored, or a removed
workflow's row-derived phase — or excluded from this cycle's computation when the workflow is
unresolved; the poller SHALL separately track whether *any* workflow was unresolved this cycle,
distinct from the phase list itself). Rule: (0) if the effective-phase list is
empty (every workflow this cycle was unresolved, or there were no
`argo_workflow_name`s and no dispatch failures to begin with), the rollup concludes nothing — no
`update_cyl_pipeline_run_status` call is made this cycle; this is a real, distinct outcome, not a
vacuous match falling through to rule (2). Otherwise: (1) if any effective phase is `Pending` or
`Running`, the run's status is `'running'`; (2) otherwise, if every effective phase is `Succeeded`,
the run's status is `'complete'`; (3) otherwise, if no effective phase is
`Succeeded`, the run's status is `'failed'`; (4) otherwise (a mix of `Succeeded` and non-`Succeeded`
terminal phases), the run's status is `'partial'`. **Rules (2), (3) and (4) are all withheld while any
workflow is unresolved: the rollup then concludes nothing and makes no call.** `'complete'` must
never be an unverified guess, and a `'failed'`/`'partial'` conclusion would close the unresolved
workflow's rows and, being final, could never be corrected; the removal rule in the poller
requirement bounds the wait. This generalizes
`_settle_cyl_pipeline_run`'s existing three-way split (which only ever considered dispatch outcome) by
adding the `'running'` branch on top of the same terminal-outcome structure.

#### Scenario: One batch still running holds the whole run at running, even if others finished

- **WHEN** a run has two batches, one whose workflow is `Succeeded` and one whose workflow is `Running`
- **THEN** the run's rollup status is `'running'`, not `'partial'` or `'complete'`

#### Scenario: A dispatch-failed batch counts as an effective Failed phase in the rollup

- **WHEN** a run has two batches, one that failed to dispatch (`status='failed'`,
  `argo_workflow_name IS NULL`) and one whose real workflow later reaches `Succeeded`
- **THEN** the run's rollup status is `'partial'` once the second batch's workflow is terminal (a mix
  of an effective `Failed` and a real `Succeeded`)

#### Scenario: Every batch succeeding marks the run complete

- **WHEN** every one of a run's batches' workflows reaches `Succeeded`
- **THEN** the run's rollup status is `'complete'`

#### Scenario: Every batch failing for real marks the run failed, not partial

- **WHEN** a run has one batch whose real workflow reaches `Failed`
- **THEN** the run's rollup status is `'failed'`, not `'partial'` (there is no successful outcome to
  make it a mix)

#### Scenario: A batch still Pending (not yet Running) holds the run at running

- **WHEN** a run has a single batch whose workflow's real phase is `Pending` and no other batch —
  i.e. the effective-phase list is exactly `['Pending']`, not mixed with any `Running` phase
- **THEN** the run's rollup status is `'running'`, identical to the `Running` case — `Pending` and
  `Running` are both non-terminal and treated as the same rollup bucket

#### Scenario: An empty effective-phase list concludes nothing, not a vacuous complete

- **WHEN** a candidate run's effective-phase list is empty this cycle (every workflow was excluded per
  the unresolved-workflow scenario, or there were no workflow names and no dispatch failures at all)
- **THEN** the rollup does not conclude `'complete'` (rule (2)'s "every phase is `Succeeded`" must not
  match vacuously against an empty list)
- **AND** no `update_cyl_pipeline_run_status` call is made for that run this cycle

#### Scenario: A `'partial'` run's still-running dispatched batch resolves to `'running'`, not stuck at `'partial'`

- **WHEN** a candidate run's current `status` is `'partial'` and its one genuinely-dispatched batch's
  workflow real phase is `Running` this cycle
- **THEN** the rollup computes `'running'` (rule (1) is checked before the terminal rules, so a
  `'partial'`-sourced run's in-flight work is not a dead end) and the poller calls
  `update_cyl_pipeline_run_status` with `'running'`

#### Scenario: A confirmed failure waits for an unresolved sibling workflow

- **WHEN** a run has one batch whose dispatch itself failed (an effective `'Failed'` phase) and one
  other batch whose workflow is unresolved this cycle
- **THEN** the rollup concludes nothing and no `update_cyl_pipeline_run_status` call is made for that
  run this cycle
- **AND** once that workflow is removed with every row `'written'`, the rollup concludes `'partial'`

#### Scenario: A garbage-collected Succeeded batch does not turn a partial run failed

- **WHEN** a dispatch-settled `'partial'` run has one dispatch-failed scan, and its one workflow
  `"wf-a"` was recorded `Succeeded` and now returns a verified NotFound
- **THEN** the effective phases are `['Failed', 'Succeeded']` and the rollup is `'partial'`, not
  `'failed'`

### Requirement: `update_cyl_pipeline_run_status` writes the rollup result under least privilege

The database SHALL provide `update_cyl_pipeline_run_status(p_run_id bigint, p_status text, p_done_count
integer DEFAULT NULL, p_failed_count integer DEFAULT NULL) RETURNS void`, `SECURITY DEFINER`,
validating `p_status` is one of `'running'|'complete'|'failed'|'partial'` (raising an error otherwise),
and updating `cyl_pipeline_runs` only when the row's current `status` is `'submitted'` or `'running'`,
or is `'partial'` with `poller_concluded_at IS NULL` — a run already `'queued'` (never dispatched),
already `'complete'`/`'failed'`, or already concluded by this function is left untouched. The update SHALL always set `status = p_status`, and SHALL set `done_count =
COALESCE(p_done_count, done_count)` and `failed_count = COALESCE(p_failed_count, failed_count)` —
passing `NULL` for either (the default) leaves that column unchanged, so existing callers that never
supply them continue to work exactly as before. On a transition into a terminal status
(`'complete'`/`'failed'`/`'partial'`), `completed_at` and
`cyl_pipeline_runs.poller_concluded_at` SHALL both be set to `now()`, replacing
any `completed_at` Phase 2's dispatch-settle wrote earlier. Because the row then no longer matches the
update's source-status guard, this happens exactly once per run: a concluded run's `status`, counts,
`completed_at` and `poller_concluded_at` never change again through this function.
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

#### Scenario: A dispatch-settled `'partial'` run is confirmed once, then final

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status = 'partial'` for a run whose
  current `status` is `'partial'` and `poller_concluded_at IS NULL` (Phase 2's dispatch-settle)
- **THEN** the call succeeds and sets `completed_at` and `poller_concluded_at` to the current time
- **AND** a second call for the same run, with `p_status` `'partial'`, `'failed'`, `'complete'` or
  `'running'` and different counts, leaves `status`, `done_count`, `failed_count`, `completed_at` and
  `poller_concluded_at` unchanged

#### Scenario: Two concurrent terminal writes conclude the run once

- **WHEN** two sessions call `update_cyl_pipeline_run_status` for the same `'running'` run at the
  same time, one with `'partial'` and one with `'failed'`
- **THEN** exactly one write takes effect, and `poller_concluded_at` and `completed_at` hold that
  write's timestamp

#### Scenario: A non-terminal write does not mark the run concluded

- **WHEN** `update_cyl_pipeline_run_status` is called with `p_status = 'running'` for a `'submitted'`
  run, or for a dispatch-settled `'partial'` run
- **THEN** the status becomes `'running'` and `poller_concluded_at` stays `NULL`

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

### Requirement: A `'complete'` rollup does not imply every scan produced a result

The rollup SHALL continue to compute run status from the *effective phases* defined by the rollup
rule — the real Argo phase per distinct workflow, plus a synthesized `'Failed'` for scans whose
dispatch failed and that have no workflow at all — and callers SHALL NOT infer per-scan completeness
from the result. Since the pipeline DAG gained its terminal exit gate, a Workflow phase of
`Succeeded` means "the gate accepted the producers' exit codes", which includes the partial-success
code `3` — so rule (2)'s `'complete'` is reachable for a run in which individual scans genuinely
failed. The poller SHALL therefore keep recording real per-scan outcomes in
`done_count`/`failed_count` independently of the phase-derived status, and no consumer of
`cyl_pipeline_runs.status` may treat `'complete'` as equivalent to `failed_count = 0`.

Three combinations follow. None is a defect in the rollup:

1. **`'complete'` with `failed_count > 0`** — reachable when `images-downloader` isolates some
   scans' failures and stages the rest; each downloader attempt writes only its own usable keys to
   its own per-run `RunManifest`, so no earlier run's or earlier attempt's keys are carried in. It
   is specific to that stage: a scan isolated later, by
   `predictor` or `trait-extractor`, is already recorded in the manifest, so write-back finds a
   declared `scan_key` with no result and exits non-zero, the gate is omitted, and a single-batch
   run reads `'failed'` (a multi-batch run whose other batches succeeded reads `'partial'`).
2. **`'complete'` with `done_count = 0`** — only for a run that enumerates zero scans. A batch
   whose `images-downloader` stages nothing writes no `RunManifest`, so each downstream reader, knowing its run identity, finds none and
   fails, and write-back closes the batch's scans out and exits non-zero: the run reads `'failed'`
   with `failed_count = scan_count`. A stale legacy `run_manifest.json` that predict or traits
   falls back to does not change this, because write-back treats a legacy file naming another run
   as no manifest for this run.
3. **`'failed'` with `done_count > 0`** — **already reachable before the exit gate**, because each
   envelope's per-scan `'written'` update commits in that envelope's own transaction, so any
   write-back that ingested some envelopes and then exited non-zero produced it. The gate does not
   create this combination; it adds a **second route** to it, by rejecting a producer exit code
   outside `{0,3}` after write-back has already committed results. Consumers must not read
   `'failed'` as "nothing was written".

**Known bound on the "read `failed_count`, not `status`" instruction.** Rules (2)–(4) withhold
every terminal conclusion while any of the run's workflows is unresolved, and skips the run entirely rather than
writing partial information. A workflow the poller saw finish keeps its stored phase after TTL
garbage collection, so this lasts only while a workflow it never saw finish is inside the removal
rule's grace period and TTL bound (see the poller requirement); after that the workflow's phase is
derived from its rows. Such a row-derived phase is `Failed` whenever any of the workflow's scans
failed, even if the real Workflow ended `Succeeded` through exit code `3`, so a run concluded that
way can read `'partial'`/`'failed'` where Argo would have given `'complete'`; the counts are exact
either way.

Pipeline-level `'partial'` (rule (4)) is unchanged in definition but narrowed in practice: it no
longer arises from partial failure *within* a batch. It still arises from terminal effective phases
differing across a run — whole batch Workflows ending differently, or a dispatch-failed batch that
never had a Workflow at all alongside one that succeeded.

#### Scenario: A downloader-stage isolation still rolls up to complete

- **WHEN** a run's only batch has `images-downloader` exit `3` after isolating one scan, and the
  exit gate accepts that code so the Workflow phase is `Succeeded`
- **THEN** the rollup returns `'complete'`
- **AND** the run's `failed_count` is non-zero, recorded from real per-scan status rather than
  inferred from the phase

#### Scenario: A failed run may have written results

- **WHEN** write-back commits results for some scans and the Workflow then ends `Failed` — whether
  because write-back itself exited non-zero and the gate was omitted, or because the gate rejected a
  producer's exit code after write-back had already committed
- **THEN** the rollup returns `'failed'`
- **AND** `done_count` is non-zero, so a consumer must not treat `'failed'` as "nothing was written"

#### Scenario: An infrastructure failure of the gate itself fails a fully successful run

- **WHEN** every scan in a run is written successfully but the `exit-gate` pod never reaches a
  terminal success — it is preempted and the node reports `Error`, or it stays `Pending` past the
  template's timeout across its retries
- **THEN** the rollup returns `'failed'` with `done_count` equal to the run's scan count and
  `failed_count` of `0` — a row that contradicts itself
- **AND** no cause is recorded in `error_message`, and the Workflow object carrying the real cause is
  TTL-GC'd, so an automated consumer that re-dispatches on `'failed'` will re-dispatch a run whose
  every scan already succeeded

#### Scenario: A batch whose downloader staged nothing rolls up to failed

- **WHEN** a run's only batch has `images-downloader` stage no scan (so no `RunManifest` is
  written), and write-back exits non-zero on the missing manifest after closing out the batch's
  scans
- **THEN** the rollup returns `'failed'`
- **AND** `done_count` is `0` and `failed_count` equals the run's scan count

## ADDED Requirements

### Requirement: Each workflow's last observed terminal phase is recorded in `cyl_pipeline_run_workflows`

The database SHALL provide a table `cyl_pipeline_run_workflows` with columns `run_id BIGINT NOT NULL`
(foreign key `cyl_pipeline_run_workflows_run_id_fkey` to `cyl_pipeline_runs(id)`),
`argo_workflow_name TEXT NOT NULL`, `phase TEXT NOT NULL` (check constraint
`cyl_pipeline_run_workflows_phase_check`: one of `'Succeeded'`, `'Failed'`, `'Error'`) and
`observed_at TIMESTAMPTZ NOT NULL DEFAULT now()`, with primary key
`cyl_pipeline_run_workflows_pkey (run_id, argo_workflow_name)`. It is not added to the Realtime
publication. Row-level security SHALL be enabled; every privilege that default privileges grant on a
new relation SHALL be revoked, then `SELECT` granted to `bloom_workflows`, `bloom_user` and
`bloom_agent` (each with a matching `SELECT` policy) and all privileges to `bloom_admin`, so that no
role but `bloom_admin` can insert, update or delete rows directly.

The database SHALL provide `record_cyl_pipeline_workflow_phase(p_run_id BIGINT, p_argo_workflow_name
TEXT, p_phase TEXT) RETURNS BOOLEAN`, `SECURITY DEFINER` with `search_path` pinned to
`pg_catalog, public`, which SHALL raise an error when `p_phase` is not one of `'Succeeded'`,
`'Failed'`, `'Error'`; SHALL write nothing and return `false` when no `cyl_pipeline_run_scans` row has
that `run_id` and `argo_workflow_name`; SHALL otherwise insert the row, or, when one exists with a
different `phase`, set `phase = p_phase` and `observed_at = now()`; and SHALL return whether a row was
inserted or changed. Concurrent calls for the same key SHALL NOT raise a unique violation.

The database SHALL provide `close_cyl_pipeline_run_workflow_scans(p_run_id BIGINT,
p_argo_workflow_name TEXT, p_error_message TEXT) RETURNS INTEGER`, `SECURITY DEFINER` with the same
pinned `search_path`, which SHALL set `status = 'failed'`, `error_message = p_error_message` and
`updated_at = now()` on the `cyl_pipeline_run_scans` rows with that `run_id` and
`argo_workflow_name` whose `status` is `'queued'`, and return how many rows it changed. It is the
run-scoped counterpart of `fail_cyl_pipeline_run_scans_without_result`, which matches on the workflow
name alone and stays in use by `bloomctl` (capability `cyl-trait-writeback`).

For both functions `EXECUTE` SHALL be revoked from `PUBLIC`, `anon` and `authenticated` and granted
only to `bloom_workflows`. The migration SHALL also add `cyl_pipeline_runs.poller_concluded_at
TIMESTAMPTZ NULL` (its meaning is defined by the `update_cyl_pipeline_run_status` requirement),
leaving it `NULL` on every existing row; SHALL set a `lock_timeout` before altering
`cyl_pipeline_runs`; SHALL be re-runnable; SHALL end by notifying PostgREST to reload its schema; and
SHALL ship a companion rollback script whose header says to redeploy code that does not use these
objects before running it.

#### Scenario: A first terminal phase is inserted

- **WHEN** `bloom_workflows` calls `record_cyl_pipeline_workflow_phase(r, 'wf-a', 'Succeeded')` and run
  `r` has a scan row with `argo_workflow_name = 'wf-a'`
- **THEN** it returns `true` and `cyl_pipeline_run_workflows` holds `(r, 'wf-a', 'Succeeded')`

#### Scenario: The same phase again changes nothing

- **WHEN** the same call is made a second time
- **THEN** it returns `false` and `observed_at` is unchanged

#### Scenario: A different terminal phase replaces the stored one

- **WHEN** `(r, 'wf-a')` is stored as `Failed` and the RPC is called with `'Succeeded'`
- **THEN** it returns `true`, `phase` is `'Succeeded'` and `observed_at` is advanced

#### Scenario: A workflow the run does not own is not recorded

- **WHEN** the RPC is called with a workflow name that none of run `r`'s scan rows carries, including
  a name that another run's rows carry
- **THEN** it returns `false` and no row is inserted

#### Scenario: A non-terminal phase is rejected

- **WHEN** the RPC is called with `p_phase` of `'Running'`, `'Pending'`, `''` or `NULL`
- **THEN** it raises an error and no row is written

#### Scenario: The run-scoped close-out leaves another run's rows alone

- **WHEN** runs `r1` and `r2` each have a `'queued'` row under `argo_workflow_name = 'wf-a'`, and
  `close_cyl_pipeline_run_workflow_scans(r1, 'wf-a', 'msg')` is called
- **THEN** it returns `1`, `r1`'s row is `'failed'` with `error_message = 'msg'`, and `r2`'s row is
  still `'queued'`
- **AND** a second identical call returns `0`, and rows already `'written'` or `'failed'` are
  unchanged

#### Scenario: Only bloom_workflows may call the functions, and no other role writes the table

- **WHEN** `has_function_privilege` is checked for `anon`, `authenticated`, `PUBLIC`, `bloom_user`,
  `bloom_writer` and `bloom_admin` against both functions, and `has_table_privilege` for `INSERT`,
  `UPDATE` and `DELETE` on `cyl_pipeline_run_workflows` for `anon`, `authenticated`, `bloom_user`,
  `bloom_writer`, `bloom_agent` and `bloom_workflows`
- **THEN** each reports `false`, and `EXECUTE` for `bloom_workflows` on both functions reports `true`
- **AND** `bloom_workflows`, `bloom_user` and `bloom_agent` can `SELECT` the table's rows, and `anon`
  cannot

#### Scenario: The functions are hardened

- **WHEN** `pg_proc` is read for both functions
- **THEN** each is `SECURITY DEFINER` with `search_path` set to `pg_catalog, public`

#### Scenario: The migration and rollback are re-runnable

- **WHEN** the migration body is applied twice, and the rollback is applied after it
- **THEN** both applications succeed, and after the rollback the table, both functions and
  `poller_concluded_at` are gone and `update_cyl_pipeline_run_status` again accepts `'partial'` as a
  source status with no finality rule
