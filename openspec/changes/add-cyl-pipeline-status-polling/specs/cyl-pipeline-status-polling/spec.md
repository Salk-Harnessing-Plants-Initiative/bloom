## ADDED Requirements

### Requirement: `k8s_client.get_workflow_status` reads a single Workflow's real phase

`services/workflows/k8s_client.py` SHALL provide `get_workflow_status(name: str) -> str | None`,
issuing `GET {API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows/{name}` with the same
bearer token and TLS configuration `submit_workflow` already uses, and returning the value of
`.status.phase` from the response body on a `2xx` response. It SHALL return `None` (not raise) when the
response is `404` — an expected condition (the Workflow already self-deleted via `ttlStrategy`, or was
deleted by an operator), not a failure. It SHALL raise a new `K8sStatusError` for any other non-2xx
response or network-level failure, constructed with a fixed, generic message — never the raw response
body, exception text, or API server URL — matching `K8sSubmissionError`'s existing sanitization
convention, since this error's message may end up in a user-facing field. `_validate_config()` SHALL be
called first, raising `K8sConfigError` before any network call if credentials are missing, identical to
`submit_workflow`.

#### Scenario: A Succeeded workflow's phase is read

- **WHEN** `get_workflow_status` is called for a workflow name whose real status is `Succeeded`
- **THEN** it returns the string `"Succeeded"`

#### Scenario: A missing workflow returns None, not an error

- **WHEN** `get_workflow_status` is called for a workflow name that returns `404`
- **THEN** it returns `None`
- **AND** no exception is raised

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
SHALL select every `cyl_pipeline_runs` row whose `status` is `'submitted'`, `'running'`, or `'partial'`
(a `'partial'` run may still have genuinely-dispatched batches whose real Argo outcome hasn't been
checked — see the `'partial'`-inclusion scenario below), and for each such run: collect the distinct
`argo_workflow_name` values from that run's `cyl_pipeline_run_scans` rows, call `get_workflow_status`
for each, compute the run's rollup status (see the rollup requirement below), and — if the computed
status differs from the run's current, already-known status — a computed status that exactly matches
the candidate row's current status is a no-op this cycle (no RPC call, no `completed_at` bump; see the
"unchanged conclusion" scenario below) — call `update_cyl_pipeline_run_status` with the result, **except**
when the computed status is `'complete'` and any of this cycle's workflow lookups returned `None` (404) —
see the rollup requirement's "withheld complete" rule. It SHALL isolate a failure fetching or updating
any one run (a K8s error, a DB-read error, or a failed write) to that run alone, never aborting the
rest of the cycle's candidates, and SHALL isolate a failure fetching the candidate list itself to that
cycle alone (retrying next cycle, not crashing). Because this per-run/per-cycle isolation means a single
sweep essentially never lets an exception propagate out of it, the poller's outer loop SHALL track
whether each cycle completed cleanly (no isolated errors) and, after `3` consecutive unclean cycles,
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
  outcome was never confirmed must not be silently treated as if it had succeeded

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

#### Scenario: A computed status matching the run's current status is a no-op

- **WHEN** a candidate run's already-known `status` is `'partial'` and this cycle's rollup also computes
  `'partial'` (no new evidence changed the outcome)
- **THEN** the poller does NOT call `update_cyl_pipeline_run_status` for that run this cycle
- **AND** `completed_at` is not touched (it does not advance on a reconfirmation of an unchanged value)

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
(looked up via `get_workflow_status`, or excluded from this cycle's computation per the
`404`-handling scenario above — the poller SHALL separately track whether *any* workflow was excluded
this cycle this way, distinct from the phase list itself). Rule: (0) if the effective-phase list is
empty (every workflow this cycle was excluded per the `404`-handling scenario, or there were no
`argo_workflow_name`s and no dispatch failures to begin with), the rollup concludes nothing — no
`update_cyl_pipeline_run_status` call is made this cycle; this is a real, distinct outcome, not a
vacuous match falling through to rule (2). Otherwise: (1) if any effective phase is `Pending` or
`Running`, the run's status is `'running'`; (2) otherwise, if every effective phase is `Succeeded`,
the run's status is `'complete'` — **unless any workflow was excluded via a 404 this cycle, in which
case the rollup withholds this conclusion and makes no call at all** (an unconfirmed workflow could
have failed; `'complete'` must never be an unverified guess); (3) otherwise, if no effective phase is
`Succeeded`, the run's status is `'failed'`; (4) otherwise (a mix of `Succeeded` and non-`Succeeded`
terminal phases), the run's status is `'partial'`. Rules (3) and (4) are NOT withheld by an excluded
workflow — once at least one effective phase is a confirmed non-`Succeeded` terminal outcome, the true
aggregate can never be `'complete'` regardless of the excluded workflow's real fate, so concluding
`'failed'`/`'partial'` remains safe even with incomplete information. This generalizes
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
  the `404`-handling scenario, or there were no workflow names and no dispatch failures at all)
- **THEN** the rollup does not conclude `'complete'` (rule (2)'s "every phase is `Succeeded`" must not
  match vacuously against an empty list)
- **AND** no `update_cyl_pipeline_run_status` call is made for that run this cycle

#### Scenario: A `'partial'` run's still-running dispatched batch resolves to `'running'`, not stuck at `'partial'`

- **WHEN** a candidate run's current `status` is `'partial'` and its one genuinely-dispatched batch's
  workflow real phase is `Running` this cycle
- **THEN** the rollup computes `'running'` (rule (1) is checked before the terminal rules, so a
  `'partial'`-sourced run's in-flight work is not a dead end) and the poller calls
  `update_cyl_pipeline_run_status` with `'running'`

#### Scenario: A confirmed failure is still concluded despite one unresolved sibling workflow

- **WHEN** a run has one batch whose dispatch itself failed (an effective `'Failed'` phase) and one
  other batch whose workflow returns `None` (404) this cycle
- **THEN** the rollup concludes `'partial'` (or `'failed'`, if no `Succeeded` phase is present at all)
  and calls `update_cyl_pipeline_run_status` with that result — the presence of an unresolved workflow
  does NOT withhold a `'failed'`/`'partial'` conclusion the way it withholds `'complete'`, since a
  confirmed non-`Succeeded` outcome already rules out the run ever being a full success

### Requirement: `update_cyl_pipeline_run_status` writes the rollup result under least privilege

The database SHALL provide `update_cyl_pipeline_run_status(p_run_id bigint, p_status text) RETURNS
void`, `SECURITY DEFINER`, validating `p_status` is one of `'running'|'complete'|'failed'|'partial'`
(raising an error otherwise), and updating `cyl_pipeline_runs` only when the row's current `status` is
`'submitted'`, `'running'`, or `'partial'` — a run already `'queued'` (never dispatched) or already
`'complete'`/`'failed'` is left untouched. On a transition into a terminal status
(`'complete'`/`'failed'`/`'partial'`), `completed_at` SHALL be set to `now()` **unconditionally** —
every call that reaches this branch advances it, not only the first (Phase 2's own dispatch-settle
write already sets `completed_at` for the common `'submitted'` case before this function is ever
called, so a guard that only fires when `completed_at IS NULL` would never actually run in production;
see `design.md`'s `completed_at` decision for why the previous `IS NULL` guard was wrong, and why an
always-fresh timestamp is preferred over comparing against the run's previous stored `status` value).
`EXECUTE` SHALL be revoked from `PUBLIC`, `anon`, and `authenticated`, and granted only to
`bloom_workflows` — the same triple-revoke/single-grant pattern every other `SECURITY DEFINER` wrapper
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
- **AND** the run's `status` and `completed_at` are unchanged

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

### Requirement: `GET /workflows/runs/{run_id}` returns current DB state without querying Argo

`services/workflows/main.py` SHALL provide `GET /workflows/runs/{run_id}` (externally reachable as `GET
/workflows/runs/{run_id}` once Caddy strips the `/workflows` prefix, matching every existing route),
requiring a valid Supabase user JWT (`Depends(require_supabase_user)`) and enforcing the same per-user
rate limit the existing routes use. It SHALL return the `cyl_pipeline_runs` row for `run_id` plus its
associated `cyl_pipeline_run_scans` rows, read directly from the database — it SHALL NOT itself query
the Argo/K8s API; live reconciliation is exclusively the poller's job (see the poller requirement
above). It SHALL respond `404` if no run with that id exists.

#### Scenario: A caller with a valid JWT reads a run's current status

- **WHEN** an authenticated request is made to `GET /workflows/runs/{run_id}` for an existing run
- **THEN** the response includes the run's current `status` and its scans' current state, exactly as
  stored — reflecting whatever the poller last wrote, not a value computed during this request

#### Scenario: A nonexistent run returns 404

- **WHEN** `GET /workflows/runs/{run_id}` is called for a `run_id` that does not exist
- **THEN** the response is `404`

#### Scenario: An unauthenticated request is rejected

- **WHEN** `GET /workflows/runs/{run_id}` is called without a valid Supabase user JWT
- **THEN** the request is rejected before any database query, matching the existing routes' auth
  behavior

#### Scenario: A rate-limited caller is rejected

- **WHEN** a caller exceeds the per-user rate limit `enforce_rate_limit` enforces on the existing
  routes
- **THEN** `GET /workflows/runs/{run_id}` responds `429` before any database query, matching the
  existing routes' rate-limit behavior
