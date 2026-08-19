## Context

bloom #11's full request-driven pipeline trigger is `POST /workflows/pipeline` → enumerate → dedup →
insert tracking rows → chunk into batches → submit one Argo workflow per batch → **poll for status**.
Phase 1 (archived `add-cyl-pipeline-trigger`) built enumerate/dedup/enqueue. Phase 2 (`add-cyl-pipeline-
dispatch`, merged, archive PR [#688](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/688)
open-but-approved) built real Argo submission. This proposal builds the "poll for status" step — the
last piece. The canonical architecture is
[sleap-roots-pipeline's 2026-07-06 A4 design doc](https://github.com/talmolab/sleap-roots-pipeline/blob/main/docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md)
(§3 step 4, §4 components, §6 execution topology); Phase 2's own `design.md` (in
`openspec/changes/add-cyl-pipeline-dispatch/`) recorded two risks this proposal inherits but does not
close (see Non-Goals).

Two real open questions were resolved with the user this session before this could be scoped (see
Decisions below): whether the poller or an on-demand `GET` drives the actual Argo query, and whether
per-scan `cyl_pipeline_run_scans.status` is in scope.

## Goals / Non-Goals

**Goals (this phase):**
- Every run stuck at `'submitted'` (all its batches got a real `argo_workflow_name`) or `'running'`
  (this phase's own intermediate state) is periodically re-checked against real Argo Workflow phases,
  and `cyl_pipeline_runs.status` is progressed to `'running'`/`'complete'`/`'failed'`/`'partial'` to
  reflect real pipeline outcome, not just dispatch outcome.
- A cheap, authenticated, DB-only `GET /workflows/runs/{id}` exists for any caller without a
  websocket/Realtime subscription (the issue's own literal title).
- The rollup rule for "one run, many batches/workflows" is deterministic and mirrors the existing
  `_settle_cyl_pipeline_run` three-way split, so a future reader only has to learn one aggregation
  pattern for this whole capability, not two unrelated ones.

**Non-goals (deferred, tracked for continuity, not implemented in this change):**
- Per-scan `cyl_pipeline_run_scans.status` (`predicted`/`written`/`reused`) — a separate mechanism (the
  write-back RPC, `insert_cyl_result_envelope`), not Argo-phase polling. Filed as
  [bloom #696](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/696).
- Reconciling the "successful-submission-recorded-as-failed" risk Phase 2's `design.md` flagged (a
  batch whose scans are `status='failed'`/`argo_workflow_name IS NULL` even though the K8s API actually
  accepted the submission, e.g. the worker crashed between `submit_workflow` succeeding and
  `complete_cyl_pipeline_batch` committing). This poller has **no workflow name to poll** for such
  scans — a real fix needs a `LIST` filtered by the `pipeline-run-id`/`batch-index`/`environment`
  labels (which Phase 2 stamped specifically so a future reconciliation sweep could do this), not the
  by-name `GET` this phase adds. Recorded here, not solved, matching Phase 2's own deferral of the same
  risk.
- The mirror-image "resubmission" risk (same `design.md` section) — likewise not addressed by a
  by-name status `GET`.
- A Workflow that self-deletes via `ttlStrategy` before this poller ever observes a terminal phase for
  it. See "Decision: poll interval vs. TTL" below.
- The Bloom web UI status panel (`Realtime` subscription per the canonical design's §10) — no UI
  proposal exists yet to build against.

## Decisions

### Decision: a periodic sweep in a new standalone poller, not the `GET` route, drives the actual Argo query

The canonical design doc is explicit: *"workflows service **polls Argo** → updates
`cyl_pipeline_runs.status`; ... Browser watches `cyl_pipeline_runs` via Supabase Realtime"* (§3.4), and
lists `GET /workflows/runs/{id}` only as "if needed" (§4) — a plain DB read, not the trigger for a live
Argo check. Making the `GET` route itself call Argo would mean a run's real status only ever updates
when *someone happens to ask* — silently wrong for the Realtime-subscribed browser UX the canonical
design commits to, since Realtime only fires on an actual DB write, not on a hypothetical read-time
computation nobody triggered. A standalone poller (mirroring `dispatch_worker.py`'s already-proven
shape: `SIGTERM`-graceful, retry-with-backoff Supabase connection, tight RPC timeout) keeps the write
path independent of whether anyone is watching.

- Alternatives considered: (a) `GET` route does live-refresh-then-read — rejected, contradicts the
  canonical design's own Realtime commitment and makes the API route's latency hostage to the K8s API's
  reachability, the same coupling Phase 2's own "separate worker, not inline" decision already rejected
  for submission. (b) both a poller and a live-refreshing `GET` — rejected as unnecessary scope for a
  phase whose actual gap (per the roadmap's own framing) is the missing write path, not a missing read
  path; a plain DB-read `GET` already satisfies the issue's literal title.

### Decision: per-scan status stays out of scope; filed as bloom #696

The canonical design's §3 step 3 places the `written` per-scan transition inside the `traits+writeback`
stage's call to `insert_cyl_result_envelope`, not inside status polling. Confirmed by grep: none of
that RPC's three migrations (`20260630180000`/`20260706170000`/`20260720000000`) ever reference
`cyl_pipeline_run_scans`. This is a real, currently-unimplemented gap in the canonical design's own
intended flow — but it is not this phase's gap to close: an Argo phase of `Succeeded` proves the pod
exited 0, nothing about which of a batch's scans were predicted vs. written vs. reused. Filed
separately rather than silently expanded into this phase's scope.

- Alternatives considered: mark all of a `Succeeded` batch's scans `'predicted'` as a rough
  approximation — rejected; it's actively wrong for a scan the cluster-side skip-if-done check found
  already-done and never touched (which should be `'reused'`, if anything), and inventing a status this
  poller can't actually justify is worse than leaving the column at its current, honest `'queued'`.

### Decision: the rollup computation happens in Python (the poller), not SQL — a deliberate departure from Phase 2's own precedent

Phase 2's `design.md` decided `_settle_cyl_pipeline_run`'s aggregation must happen inside the
`SECURITY DEFINER` function, specifically to prevent a **race between two workers settling the last two
batches of the same run at the same moment** — a read-then-write round trip from Python could lose that
race. That race doesn't exist here in the same shape: this poller's aggregation input (each batch's
real Argo phase) can only be obtained by calling out to the K8s API, which SQL cannot do — the fetch
step is unavoidably Python-side. And unlike a one-shot "settle this batch exactly once" event, this
poller's write is an **idempotent recompute from current external truth**, not an accumulation: Argo
phases move monotonically forward (`Pending → Running → Succeeded/Failed`, never backward), so even two
overlapping poll cycles computing slightly different snapshots and racing to write just means the run's
status may briefly reflect a very-slightly-stale-but-still-valid reading before the next cycle
corrects it — never a lost update, never a permanently wrong terminal value. The new RPC's own guard
(only writes a run currently `'submitted'`/`'running'`) additionally makes it a no-op once a run is
truly terminal, closing the tail case a naive "always overwrite" policy would otherwise leave open.

- Alternatives considered: pass raw per-workflow phases into the RPC and let SQL apply the CASE
  (mirroring Phase 2's structure literally) — considered and rejected only because it adds a `jsonb`
  parameter and a second place the same rollup logic could drift from its unit-tested Python version,
  for a race this specific write doesn't actually have; revisit if a future need (e.g. multiple poller
  replicas polling the *same* run concurrently, which nothing in this phase requires) makes the
  distinction matter.

### Decision: `get_workflow_status` is a by-name `GET`, not a label-filtered `LIST`

`k8s_client.py` already POSTs to the Workflow collection endpoint and reads `metadata.name` back from
the response (`submit_workflow`); every `cyl_pipeline_run_scans.argo_workflow_name` this poller needs
to check came from exactly that response. A `GET .../workflows/{name}` is the direct, already-proven
resource path (same base URL Phase 2 live-validated, one path segment further) with a single object in
the response to parse — `.status.phase`. A label-filtered `LIST` (`?labelSelector=pipeline-run-id=42`)
would let one call cover a whole run's batches at once, but it is unvalidated against the real cluster,
introduces query-string construction this module has never needed before, and is exactly the
mechanism the deferred reconciliation-sweep work (Non-Goals) will need anyway for the harder case (no
name to look up by at all) — better proven there, once, than half-built here for a case where the
simpler by-name `GET` already fully suffices.

- Alternatives considered: label-filtered `LIST` for this phase too, batching all of a run's workflow
  lookups into one call — rejected for now (unvalidated, and batches per run are typically few, so the
  per-workflow `GET` count is small); revisit if per-run batch counts grow enough that N `GET`s per
  poll cycle becomes a real cost.

### Decision: a `404` from `get_workflow_status` is treated as "unknown this cycle," not a failure

`ttlStrategy.secondsAfterCompletion` (Phase 2) means a Workflow can legitimately vanish once it's
terminal. If the poller hasn't yet observed a terminal phase for a workflow before it's cleaned up,
`get_workflow_status` returns `None` — the poller logs a warning and **skips** that workflow's
contribution to this cycle's rollup (it does not guess `Succeeded` or `Failed`), leaving the run at its
current status for another cycle rather than writing a possibly-wrong terminal value from no evidence.
If every one of a run's workflows is skipped this way this cycle, the effective-phase list ends up
empty — this is exactly rule (0) of the `cyl-pipeline-status-polling` spec's "Rollup rule" requirement
(conclude nothing, make no RPC call), not a separate case; it exists as a named rule there specifically
so an empty list can't fall through to a later rule's vacuous match (e.g. "every phase is `Succeeded`"
being trivially true over zero phases). This is a real, accepted gap: if `WORKFLOWS_STATUS_POLL_SECONDS`
is ever configured close to or above
`WORKFLOWS_K8S_TTL_SECONDS` (default `3600`), a genuinely-completed run could sit at `'running'`
indefinitely once every one of its workflows has been swept. Operationally, the poll interval must stay
well under the TTL — documented here and in `README.md`, not enforced in code (no data model exists to
validate one config value against another across two different services' env blocks).

- Alternatives considered: treat a `404` as `Succeeded` (optimistic) — rejected, actively wrong for a
  workflow that was never observed running at all (e.g. deleted by an operator, or a mis-recorded
  name); treat it as `Failed` (pessimistic) — rejected, equally capable of being wrong for the common
  case (it finished successfully and TTL'd out before the first poll happened to land).

### Decision: `status_poller.py` is a separate process from `dispatch_worker.py`

The two have genuinely different triggers: `dispatch_worker.py` reacts to new pgmq messages (event-
driven, blocks/sleeps only when the queue is empty); this poller runs on a fixed wall-clock cadence
regardless of dispatch activity, sweeping every currently-active run. Merging them into one process
would mean a slow or stuck poll sweep could starve the dispatch loop's latency (or vice versa) for no
reason — the two loops share no state and would only be combined for deployment convenience, which
this repo's own precedent (a separate `video-worker` container, a separate `cyl-pipeline-worker`
container) already rejects in favor of one script/one job/one container.

- Alternatives considered: fold polling into `dispatch_worker.py` as a second inner loop — rejected;
  couples two independently-scheduled concerns and complicates both `SIGTERM` handling (which loop
  finishes first?) and testing (mocks for one loop's seam would leak into the other's).

## Risks / Trade-offs

- **The two risks Phase 2's `design.md` already flagged (successful-submission-recorded-as-failed;
  resubmission) remain open** — see Non-Goals. This proposal's by-name `GET` cannot close either; both
  need a label-based `LIST` this proposal deliberately does not build (see the `LIST` vs `GET`
  decision above).
- **Poll interval vs. TTL is an unenforced operational constraint**, not a code-level guarantee — see
  the `404`-handling decision above. A misconfigured deploy (poll interval too close to or above TTL)
  degrades silently: affected runs simply stop progressing past `'running'`, with no error anywhere.
- **N workflow-status `GET`s per run per poll cycle** (one per distinct `argo_workflow_name`, not one
  per run) — acceptable at today's `BATCH_SIZE=25`-chunked, low-concurrency usage; revisit the `LIST`
  alternative if real usage grows enough to make this a meaningful K8s API load.
- **`update_cyl_pipeline_run_status`'s guard silently no-ops on a run that raced past `'submitted'`/
  `'running'` between the poller's read and its write** (e.g. an operator manually corrected a run's
  status out-of-band in the same window) — an accepted, narrow window; the next poll cycle simply
  excludes that run from its candidate set once its status is actually terminal, so this self-heals
  rather than requiring a retry.

## Migration Plan

Forward-only migration `<timestamp>_add_cyl_pipeline_run_status_polling.sql` (the one new `SECURITY
DEFINER` function and its `EXECUTE` grant only — no table/column changes; the `CHECK` constraint
already allows every status value this function ever writes), plus a companion rollback under
`supabase/rollbacks/`, matching this program's established pattern.

## Open Questions

- Whether a future reconciliation sweep (label-based `LIST`, closing the two risks Phase 2 flagged) is
  its own phase/change or folds into a later revision of this one — not resolved here, tracked as a
  known gap in both this file and bloom #11's own history.
- Whether `status_poller.py` needs more than one replica before real usage justifies it — like
  `dispatch_worker.py`, this is a deploy-time scaling knob, not a design change, if/when needed; unlike
  `dispatch_worker.py`, there is no pgmq-style claim to make concurrent replicas automatically safe, so
  multiple replicas today would just mean redundant, harmless, overlapping `GET`s (idempotent
  recompute — see the Python-rollup decision above) rather than a race to fix first.
