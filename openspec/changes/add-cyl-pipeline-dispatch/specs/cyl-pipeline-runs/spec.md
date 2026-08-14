## RENAMED Requirements

- FROM: `### Requirement: pgmq dispatch queue with a least-privilege enqueue function`
- TO: `### Requirement: pgmq dispatch queue with least-privilege enqueue/claim/complete/fail functions`

## MODIFIED Requirements

### Requirement: Role-based read/write access matches the standard cyl_* convention

Both tables SHALL have Row Level Security enabled. `bloom_admin` SHALL hold `FOR ALL` access.
`bloom_agent` and `bloom_user` SHALL hold read-only (`SELECT`) access, matching the repo-wide
convention that `bloom_user` no longer receives write policies. `bloom_workflows` SHALL hold
`SELECT` and a **column-scoped** `INSERT` on both tables — scoped to exactly the columns
`services/workflows/pipeline.py` populates (`target_level, target_id, params, requested_by, status,
scan_count, reused_count` on `cyl_pipeline_runs`; `run_id, scan_id, batch_index, status` on
`cyl_pipeline_run_scans`), matching this repo's own column-scoped-grant precedent for the same role
(`20260716000000_create_workflows_role.sql`'s `GRANT INSERT (scan_id, path, frames) ON
cyl_scan_videos`) rather than a blanket whole-table `INSERT`. It SHALL **NOT** hold `UPDATE`: no code
path updates either table's rows via caller privilege — the dispatch worker's
`claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/`fail_cyl_pipeline_batch` functions (see the
`pgmq dispatch queue...` requirement below) write `argo_workflow_name`/`status`/`attempts`/
`error_message`/`submitted_at`/`completed_at` as `SECURITY DEFINER`, under the function owner's
privileges, not `bloom_workflows`'s own — so no `UPDATE` grant is added for this or any other reason.
No role other than `bloom_admin` and `bloom_workflows` SHALL hold write access to either table.

#### Scenario: bloom_user can read but not write

- **WHEN** a session with role `bloom_user` attempts `SELECT` on `cyl_pipeline_runs`
- **THEN** the query succeeds
- **WHEN** the same session attempts `INSERT` or `UPDATE` on `cyl_pipeline_runs`
- **THEN** the database rejects it with an insufficient-privilege error

#### Scenario: bloom_workflows can insert but has no direct UPDATE grant, by design

- **WHEN** a session with role `bloom_workflows` inserts a row into `cyl_pipeline_runs`
- **THEN** the insert succeeds
- **WHEN** the same session attempts to `UPDATE` that row's `status` column directly (not via one of
  the `SECURITY DEFINER` wrapper functions)
- **THEN** the database rejects it with an insufficient-privilege error — this is a permanent design
  decision (see the `cyl-pipeline-dispatch` change's `design.md`), not a placeholder awaiting a future
  grant

#### Scenario: bloom_workflows can insert only the columns it actually populates

- **WHEN** a session with role `bloom_workflows` inserts a `cyl_pipeline_run_scans` row supplying only
  `run_id`, `scan_id`, `batch_index`, `status`
- **THEN** the insert succeeds
- **WHEN** the same session attempts to insert a row that also supplies `argo_workflow_name`
- **THEN** the database rejects it with an insufficient-privilege error (proving the `INSERT` grant is
  column-scoped, not whole-table)

#### Scenario: anon has no access

- **WHEN** an unauthenticated (`anon`) request queries either table
- **THEN** Row Level Security denies all rows (no policy grants `anon` access)

### Requirement: pgmq dispatch queue with least-privilege enqueue/claim/complete/fail functions

The database SHALL provide a pgmq queue named `cyl_pipeline_dispatch` (via `pgmq.create`, guarded by
an existence check so re-applying is a no-op) and the following `SECURITY DEFINER` functions, all with
`EXECUTE` revoked from `PUBLIC`, `anon`, and `authenticated` and granted only to `bloom_workflows` —
matching the explicit triple-revoke bloom PR #469's pgmq precedent found necessary, since Supabase's
default privileges grant new public-schema functions `EXECUTE` to `anon`/`authenticated` directly.
Every write these functions make (to `cyl_pipeline_runs`/`cyl_pipeline_run_scans`) happens under the
function owner's privileges as `SECURITY DEFINER` — `bloom_workflows` itself is granted no direct
`UPDATE` on either table, matching the existing "SHALL NOT hold `UPDATE`" boundary this capability
already establishes elsewhere, unchanged by this addition:

- `enqueue_cyl_pipeline_batch(p_run_id bigint, p_batch_index integer, p_scan_ids bigint[]) RETURNS
  bigint` — sends one message per batch, returning the pgmq `msg_id` (unchanged from Phase 1).
- `claim_cyl_pipeline_batch(p_vt integer DEFAULT <default>, p_max_reads integer DEFAULT <default>)
  RETURNS TABLE(run_id bigint, batch_index integer, scan_ids bigint[], msg_id bigint)` — reads the
  next message (hiding it from other callers for `p_vt` seconds) and returns nothing if the queue is
  empty. If the message has been redelivered more than `p_max_reads` times (a poison message — every
  previous claimant crashed before ever calling `complete`/`fail`), it instead marks that batch's scan
  rows `status = 'failed'`, archives the message, runs the same run-completion aggregation check
  described under `complete_cyl_pipeline_batch` below, and returns nothing to the caller.
- `complete_cyl_pipeline_batch(p_run_id bigint, p_batch_index integer, p_msg_id bigint, p_scan_ids
  bigint[], p_argo_workflow_name text) RETURNS void` — sets `argo_workflow_name` on every
  `cyl_pipeline_run_scans` row matching `(run_id, scan_id) for scan_id in p_scan_ids`, deletes the
  message, and — atomically, in the same statement, so two workers settling the last two batches of one
  run at the same moment cannot race past each other — updates `cyl_pipeline_runs.status` for
  `p_run_id` to `'submitted'` if no scan row for that run remains without a terminal outcome (a
  non-null `argo_workflow_name` or `status = 'failed'`) and every one of them succeeded, or `'partial'`
  if the run is fully settled with a mix of outcomes. A call whose batch was already settled by a prior
  call (the message's visibility timeout expired and it was redelivered and completed twice) is a
  no-op on the second call, not an error.
- `fail_cyl_pipeline_batch(p_run_id bigint, p_batch_index integer, p_msg_id bigint, p_scan_ids
  bigint[], p_error text) RETURNS void` — sets `status = 'failed'`, `error_message`, and increments
  `attempts` on every scan row in the batch, archives (dead-letters) the message, and runs the same
  run-completion aggregation check as `complete_cyl_pipeline_batch` — additionally setting
  `cyl_pipeline_runs.status` to `'failed'` if the run is fully settled and every one of its batches
  failed. A call whose batch's scan rows were already marked `'failed'` by a prior call (the same
  redelivery scenario `complete_cyl_pipeline_batch` guards against) is a no-op on the second call, not
  a double-increment of `attempts` or an overwrite of `error_message`.

#### Scenario: bloom_workflows can enqueue a batch

- **WHEN** a session with role `bloom_workflows` calls `enqueue_cyl_pipeline_batch` with a valid
  `run_id`, `batch_index`, and non-empty `scan_ids` array
- **THEN** the call succeeds and returns a pgmq `msg_id`
- **AND** a corresponding message is readable from the `cyl_pipeline_dispatch` queue

#### Scenario: bloom_workflows can claim, then complete, a batch

- **WHEN** a session with role `bloom_workflows` calls `claim_cyl_pipeline_batch` after a batch has
  been enqueued
- **THEN** it receives that batch's `run_id`, `batch_index`, `scan_ids`, and `msg_id`
- **WHEN** it then calls `complete_cyl_pipeline_batch` with those values and a workflow name
- **THEN** every one of that batch's `cyl_pipeline_run_scans` rows has that `argo_workflow_name`
- **AND** a subsequent claim does not return the same message again

#### Scenario: A claimed batch not settled within its visibility timeout is reclaimable

- **WHEN** a batch is claimed and neither `complete_cyl_pipeline_batch` nor `fail_cyl_pipeline_batch`
  is called before the visibility timeout elapses
- **THEN** a subsequent `claim_cyl_pipeline_batch` call can claim the same message again

#### Scenario: A batch redelivered past the max-reads threshold is dead-lettered on claim, and the run is still correctly settled

- **WHEN** a message has been read more times than the configured maximum without ever being
  completed or failed, and it is the last outstanding batch of its run
- **THEN** `claim_cyl_pipeline_batch` marks that batch's scan rows `'failed'`, archives the message,
  returns no batch to the caller, **and** updates `cyl_pipeline_runs.status` to `'failed'` or
  `'partial'` per the same aggregation rule `fail_cyl_pipeline_batch` uses — the run is never left
  stuck at `'queued'`/`'submitted'` merely because its last batch happened to be dead-lettered by
  `claim` rather than explicitly failed

#### Scenario: Completing the last outstanding batch of a run marks the run submitted

- **WHEN** a run has two batches, and the first was already completed successfully
- **THEN** completing the second batch successfully updates `cyl_pipeline_runs.status` to
  `'submitted'`

#### Scenario: A run with a mix of submitted and failed batches is marked partial

- **WHEN** a run's batches all reach a terminal outcome, and at least one succeeded and at least one
  failed
- **THEN** `cyl_pipeline_runs.status` is `'partial'`

#### Scenario: A run whose every batch fails is marked failed, not partial

- **WHEN** a run has one batch, and it fails submission
- **THEN** `cyl_pipeline_runs.status` is `'failed'`, not `'partial'` (there is no successful batch to
  make it a mix)

#### Scenario: A failed batch's scan rows record an incremented attempt count

- **WHEN** `fail_cyl_pipeline_batch` is called for a batch
- **THEN** every scan row in that batch has its `attempts` column incremented by 1

#### Scenario: Completing an already-settled batch a second time is a harmless no-op

- **WHEN** `complete_cyl_pipeline_batch` is called for a batch whose message was already deleted by an
  earlier, successful call for the same `msg_id` (e.g. a redelivered-then-re-completed message)
- **THEN** the second call does not raise, and the batch's scan rows and the run's status are
  unchanged from what the first call already set

#### Scenario: Failing an already-failed batch a second time is a harmless no-op

- **WHEN** `fail_cyl_pipeline_batch` is called for a batch whose scan rows were already marked
  `'failed'` by an earlier call for the same `msg_id` (e.g. a redelivered-then-re-failed message)
- **THEN** the second call does not raise, and `attempts`/`error_message`/`updated_at` are unchanged
  from what the first call already set — not double-incremented or overwritten

#### Scenario: EXECUTE is denied to anon, authenticated, PUBLIC, and every session role for every function

- **WHEN** `has_function_privilege` is checked for `anon`, `authenticated`, the implicit `PUBLIC`
  grantee, and `bloom_user`/`bloom_writer`/`bloom_admin` against any of the four functions' signatures
- **THEN** each reports `EXECUTE` as `false`
- **AND** the same check for `bloom_workflows` reports `true`
