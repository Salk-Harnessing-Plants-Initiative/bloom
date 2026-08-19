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
- Populating `cyl_pipeline_runs.error_message` from a failed workflow's real Argo status — see the
  round-2 fix decision below; `update_cyl_pipeline_run_status` writes `status`/`completed_at` only.

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

### Decision (fix, `/review-pr` round 1): a *partial* 404 must not let the rollup conclude `'complete'`

The decision above only reasoned about the *all*-404'd case (safe: rule (0) concludes nothing). Found
on review — a real, silent-corruption bug, not just a stuck-run inconvenience: if a run has two
batches, A `Succeeded` (observed this cycle) and B TTL-expired before ever being observed as terminal
(404 → excluded per the decision above), `effective_phases` ends up `["Succeeded"]` — B is simply
*absent*, not zeroed or flagged — and rule (2)'s `all(p == "Succeeded" ...)` is vacuously true over
what remains. The run gets written `'complete'` even though B's real outcome (possibly a failure) was
never actually confirmed. This requires no misconfiguration at all, only that sibling batches within
one run finish at different times relative to `ttlStrategy`'s window — an ordinary occurrence, not an
edge case.

**Fix**: `_fetch_effective_phases` now returns `(phases, any_unknown)` — `any_unknown` is `True` if
*any* workflow this cycle returned `None` (404). `sweep_once` computes `rollup(phases)` as before, but
if the result is `'complete'` **and** `any_unknown` is `True`, it withholds the conclusion this cycle
(logs a warning, makes no RPC call) rather than writing it. `'failed'`/`'partial'` conclusions are
**not** withheld under the same condition: if the *observed* phases already include a real `Failed`,
the true aggregate can never be `'complete'` regardless of what the missing workflow turns out to be,
so `'failed'`/`'partial'` remain safe conclusions even with an unknown entry present — only `'complete'`
is an unsafe over-claim of full success built from incomplete information.

- Alternatives considered: track *which specific* workflow is unknown and reconcile it via a
  label-based `LIST` before concluding — rejected for this fix; that's the same deferred
  reconciliation-sweep machinery the Non-Goals section already defers (no name to look up an
  already-vanished Workflow by), and withholding the conclusion is a correct, minimal stopgap that
  requires no new K8s API surface. A future reconciliation sweep can still resolve these runs; this fix
  only prevents them from being wrongly marked `'complete'` in the meantime.

### Decision (fix, `/review-pr` round 1): `'partial'` runs are included in the polling candidate set

Found on review — a real coverage bug: `_fetch_candidate_runs` originally selected only
`status IN ('submitted', 'running')`. But Phase 2's `_settle_cyl_pipeline_run` can settle a run
straight to `'partial'` the moment it has *any* dispatch-failed scan alongside *any*
successfully-dispatched one — and `'partial'` is itself already a terminal value from Phase 2's own
three-way split. Because it was excluded from the candidate query, the scans that **did** reach Argo
in such a run were never polled at all — permanently freezing the run at "partial" regardless of
whether that dispatched portion later succeeds or genuinely fails at the pipeline level. This directly
defeats this phase's own purpose for exactly the population of runs where the real outcome matters
most (a run that's already known to have some real, running work in flight).

**Fix**: `_fetch_candidate_runs` now selects `status IN ('submitted', 'running', 'partial')`, and
`update_cyl_pipeline_run_status`'s guard is widened to match (`WHERE status IN ('submitted', 'running',
'partial')`). Dispatch-failed scans still contribute an effective `'Failed'` phase (unchanged), so a
`'partial'` run's rollup can never conclude `'complete'` (rule (2) can never be satisfied while a
`'Failed'` entry is present) — it will resolve to `'partial'` again (a mix) or `'failed'` (if the
dispatched portion also failed for real), reflecting the *real*, pipeline-level outcome instead of the
frozen dispatch-time guess.

**Known, accepted trade-off — not fixed here**: once a `'partial'` run's dispatched batches are fully
resolved (no more `Pending`/`Running` among them), the run keeps satisfying the candidate query forever
(there is no per-workflow "already confirmed, stop re-checking" marker — that needs per-scan status
tracking, deferred to [bloom #696](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/696)).
Every subsequent cycle will recompute the same `'partial'` conclusion and call `update_run_status`
again — harmless to the *status* value (idempotent recompute, same as every other candidate), but see
the `completed_at` decision below for the one place this repeated re-write is user-visible.

- Alternatives considered: add a distinct status value for "dispatch-partial, pipeline-outcome
  pending" — rejected as unnecessary schema/CHECK-constraint churn for a distinction the existing
  effective-phase computation already captures correctly; only the *candidate selection* needed
  fixing, not the status vocabulary.

### Decision (fix, `/review-pr` round 1): `completed_at` always advances on a real terminal conclusion

Found on review — a real, cross-validated bug: Phase 2's `_settle_cyl_pipeline_run` unconditionally
stamps `completed_at = now()` the instant a run's *dispatch* outcome settles — including the common
`'submitted'` branch, where dispatch merely succeeded and Argo hasn't even started running yet. This
phase's original `update_cyl_pipeline_run_status` only stamped `completed_at` `WHEN ... completed_at
IS NULL` — but by the time this poller ever calls it, that column is already non-NULL from Phase 2's
own dispatch-time write. Net effect: `completed_at` silently froze near `created_at` forever, never
advancing to when the pipeline actually finished — directly contradicting this whole phase's purpose.

**Fix**: the `IS NULL` guard is removed. `update_cyl_pipeline_run_status` now stamps
`completed_at = now()` unconditionally whenever `p_status` is a real terminal value
(`'complete'`/`'failed'`/`'partial'`), on every call that reaches that branch — not just the first.
This is safe for `'submitted'`/`'running'`-sourced runs (each transitions into a terminal state at
most once, since the outer `WHERE` excludes already-terminal rows from matching again) and, per the
`'partial'`-candidate decision above, means a `'partial'` run's `completed_at` **can** advance more
than once — each poll cycle that reconfirms `'partial'` (or resolves it to `'failed'`) bumps
`completed_at` forward to that cycle's time. This is an accepted, documented consequence of the
`'partial'`-repolling trade-off above, not a new bug: the *status* value is always correct; only
`completed_at`, for this one status, reflects "the last cycle that confirmed this outcome" rather than
"the first" until bloom #696's per-scan tracking closes the "stop re-checking" gap.

- Alternatives considered: only stamp `completed_at` when `p_status IS DISTINCT FROM` the run's current
  stored `status` — rejected; a `'partial'` run whose dispatched batches finish for real can resolve to
  the *same* `'partial'` value it already had (a mix that stays a mix), and comparing against the old
  value would silently skip stamping `completed_at` for exactly that case — reintroducing a milder
  version of the same bug this fix closes, just for `'partial'` specifically.

### Decision (fix, `/review-pr` round 1): DB-read failures inside a sweep are isolated the same as K8s/write failures

Found on review: `sweep_once`'s per-run `try`/`except` originally only caught `(K8sConfigError,
K8sStatusError)` around `_fetch_effective_phases`, and `_fetch_candidate_runs` itself (the loop's own
iterable) had no guard at all — a generic DB-level exception (a transient PostgREST error, a timeout
against the tight `SINGLE_ROW_RPC_TIMEOUT_SECONDS`, or the exact deadlock class this PR's own CI run
hit) would propagate uncaught, aborting the rest of that cycle's candidates (or, for
`_fetch_candidate_runs`, the entire cycle) — inconsistent with the PR's own stated "a problem isolated
to one run... must not silently skip the rest of that cycle's candidates," which only ever covered the
K8s/write side.

**Fix**: `_fetch_effective_phases`'s per-run exception handling is widened from
`(K8sConfigError, K8sStatusError)` to a bare `Exception`, matching the write side's existing broad
catch. `_fetch_candidate_runs` is now called inside `sweep_once`'s own `try`/`except`; a failure there
logs a warning and returns immediately (equivalent to "no candidates this cycle"), leaving every run to
be re-attempted next cycle rather than crashing the whole sweep. Neither change alters `run()`'s
outer-loop reconnect behavior — this only narrows the window where a purely transient DB blip escapes
per-cycle isolation.

### Decision (fix, `/review-pr` round 1): `K8sConfigError`'s message no longer says "dispatch worker"

Found on review: `k8s_client._validate_config()` is shared by `submit_workflow` (the dispatch worker)
and this phase's `get_workflow_status` (the status poller), but its message was hardcoded to
`"dispatch worker not configured: ..."` — misleading whoever is on call when the *status poller*, not
the dispatch worker, is the one actually misconfigured. Fixed to a caller-neutral
`"K8s client not configured: ..."`.

### Decision (fix, `/review-pr` round 1): `cyl-status-poller` gets its own, larger `stop_grace_period`

Found on review: the new compose service had no `stop_grace_period` override (Docker's 10s default),
unlike its sibling `cyl-pipeline-worker` (`30s`, sized for one K8s POST + one RPC per claim).
`status_poller.py`'s `_stop()` is documented and tested to let an in-flight sweep finish — but one
sweep can issue **one `get_workflow_status` GET per distinct workflow name across every candidate run**
this cycle, not a single call, so `cyl-pipeline-worker`'s 30s isn't necessarily enough headroom either.
Set to `60s` in both compose files, with a comment explaining the N-calls-per-sweep reasoning so a
future reader doesn't assume it was copied from the sibling service without thought.

### Decision (fix, `/review-pr` round 2): `run()` reconnects after consecutive error cycles, not by catching `sweep_once`'s own exceptions

Found on review — a regression introduced by the round-1 "DB-read failures are isolated" fix above:
widening `sweep_once`'s per-run and per-cycle catches to a bare `Exception` means `sweep_once` itself
now almost never raises — every realistic failure mode (a K8s error, a DB-read error, a lost RPC write)
is caught and logged *inside* `sweep_once`, not re-raised. But `run()`'s reconnect logic lives entirely
in its own `except Exception` around the `sweep_once(client)` call — the exact mechanism meant to catch
"the Supabase client session has genuinely died" now has almost nothing left to catch. If the client
session dies for real (not a transient blip), the poller loops forever, logging a warning every cycle,
never reconnecting.

**Fix**: `sweep_once` now returns `bool` — `True` if the cycle completed with no errors at any candidate
or the candidate-fetch step, `False` if any error was caught and isolated during the cycle (the per-run
`continue`/early-`return` paths already added in round 1 now set a local `ok = False` instead of silently
swallowing that information). `run()` tracks `consecutive_error_cycles`; a clean cycle resets it to `0`,
an unclean one increments it, and once it reaches `_MAX_CONSECUTIVE_ERROR_CYCLES` (`3`), `run()`
proactively fetches a fresh `app_client()` and resets the counter — the same self-healing behavior the
pre-round-1 code got "for free" via propagation, now made explicit since propagation no longer happens.
The outer `try`/`except Exception` around `sweep_once(client)` itself is kept (not removed) — it now
covers only a genuinely-unexpected bug *inside* `sweep_once`'s own control flow (e.g. a `KeyError` on
`run["id"]`), a narrower but still real residual case, and reconnects immediately on that path exactly
as before.

Three consecutive cycles (not one) was chosen deliberately: a single isolated error (one run's transient
K8s blip) must not trigger a reconnect — that would reintroduce needless reconnect churn for exactly the
transient-error case round 1's isolation fix was built to tolerate. Only a *sustained* run of unclean
cycles — consistent with a genuinely dead session rather than one unlucky candidate — should force a
fresh connection.

- Alternatives considered: have each isolated `except` block re-raise a dedicated sentinel exception
  after logging, letting the outer `except Exception` catch that — rejected; it re-couples per-run
  isolation to the outer loop's control flow (the first isolated error in a cycle would still abort the
  rest of that cycle's candidates, the exact bug round 1 fixed) unless every call site is restructured to
  finish the cycle before raising, which is just a more convoluted way of building the same boolean this
  fix returns directly. Reconnecting on the very first unclean cycle — rejected as too aggressive; most
  unclean cycles are exactly the transient, single-run blips isolation is designed to ride out without
  disturbing the rest of the sweep.

### Decision (fix, `/review-pr` round 2): repeated same-value `'partial'`/`'running'` reconfirmation no longer rewrites the row

Found on review — a real, if cosmetic, consequence of the round-1 `'partial'`-candidate fix: a
`'partial'` run whose dispatched batches are all already resolved keeps satisfying
`_fetch_candidate_runs`'s query forever (documented already as an accepted trade-off), and every cycle
re-writes the *same* `'partial'` conclusion — which, per the `completed_at` decision above, bumps
`completed_at` forward every single cycle indefinitely. That's a real, user-visible correctness issue for
`completed_at` specifically (it should reflect when the run's outcome was last *confirmed to have
changed*, not merely "the last time a poller happened to look"), not just wasted RPC calls.

**Fix**: `_fetch_candidate_runs` now also selects `status` (not just `id`). `sweep_once` compares the
freshly computed rollup `status` against the candidate row's already-known `status`; if they match, the
cycle treats this run as unchanged and skips the `update_run_status` call entirely (no RPC write, no
`completed_at` bump, no log line) rather than re-confirming a conclusion nothing about. A real
transition — including `'submitted'`/`'running'` sourced into any terminal value, and a `'partial'` run
whose outcome *changes* to `'failed'` or resolves to a still-different `'partial'` mix in some
theoretical future multi-value scheme — still writes normally, since the computed and known values only
match once the run has already fully stabilized at that exact value.

- Alternatives considered: skip the write only for `'partial'` specifically (narrower fix, matching the
  round-1 trade-off note's literal scope) — rejected in favor of the general same-value skip above; a
  `'running'`-sourced run reconfirmed `'running'` cycle after cycle (still in progress, no new evidence)
  gets the identical wasted-write treatment today, and the general fix is no more complex than a
  status-specific one.

### Decision (fix, `/review-pr` round 2): `'partial'` **can** roll up to `'running'` — corrected wording, no code change

The round-1 `'partial'`-candidate decision's own prose ("it will resolve to `'partial'` again... or
`'failed'`") is factually incomplete, found on review: the rollup rule's ordering (rule (1), any
`Pending`/`Running` phase, is checked *before* the terminal rules) means a `'partial'` run with at least
one dispatched batch still genuinely in flight rolls up to `'running'`, not only `'partial'`/`'failed'`.
This is already correct, intended behavior per the rule as originally approved — a `'partial'` run's
dispatched-and-actually-running batches must be able to progress to `'running'` like any other candidate,
otherwise `'partial'` would be a dead end for the in-flight portion. Corrected here as a documentation-only
fix; no test or implementation change, since the rollup function already returns `'running'` correctly in
this case and existing tests (`rollup(["Running"]) == "running"`, etc.) already cover the rule ordering —
the gap was only ever in this file's own prose, not in the code or its test coverage for the general rule.
Follow-up: a new sweep-level test drives this exact transition end-to-end for `'partial'`-sourced runs
specifically (see tasks.md's round-2 section) since no existing test exercised a `'partial'`-candidate row
resolving to `'running'` at the `sweep_once` level, only the pure `rollup()` function in isolation.

### Decision (fix, `/review-pr` round 2): `error_message` is explicitly out of scope, added to Non-Goals

Found on review: `cyl_pipeline_runs.error_message` (populated by Phase 2's dispatch-time settle) is never
touched by `update_cyl_pipeline_run_status` — a `'failed'`/`'partial'` conclusion this poller writes
carries no explanation of *which* workflow failed or why, unlike a dispatch-time failure. This was an
oversight of omission (never decided against, just never decided at all) rather than a considered
Non-Goal — added explicitly below so it reads as a deliberate deferral, not a gap nobody noticed. A real
fix needs a place to put "workflow X failed" text derived from Argo's own status (e.g. `status.message` on
a `Failed`/`Error` phase) plumbed through `_fetch_effective_phases` into the RPC as a new parameter — real
scope, not a one-line addition, and this proposal's own by-name, phase-only `GET` doesn't currently even
fetch that field. Deferred rather than expanded into this round's fix pass.

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
  `'running'`/`'partial'` between the poller's read and its write** (e.g. an operator manually corrected
  a run's status out-of-band in the same window) — an accepted, narrow window; the next poll cycle
  simply excludes that run from its candidate set once its status is actually terminal, so this
  self-heals rather than requiring a retry.
- **A `'partial'` run's `completed_at` can advance more than once, and the run itself is polled
  forever once its dispatched batches are fully resolved** — see the `'partial'`-candidate and
  `completed_at` decisions above. Cosmetic/wasted-work, not a data-integrity bug: the `status` value
  itself is always correct. The round-2 same-value-skip fix stops the repeated `completed_at` bump once
  a run's conclusion stabilizes, but the run remains in the candidate query (and keeps costing one
  `GET` per distinct workflow name per cycle) forever — closing that fully needs the per-scan tracking
  deferred to bloom #696.
- **A workflow confirmed-observed once, then 404'd on a later cycle, can permanently block `'complete'`**
  (found `/review-pr` round 2): the 404-is-unknown decision above only reasons about a workflow *never*
  observed as terminal before it TTL's out. But nothing prevents this poller from observing, say,
  `Running` on cycle N, then 404 on cycle N+1 if `WORKFLOWS_STATUS_POLL_SECONDS` and
  `WORKFLOWS_K8S_TTL_SECONDS` are close enough together that a workflow can go terminal *and* TTL-expire
  entirely between two consecutive sweeps — `any_unknown` is set every such cycle, permanently withholding
  `'complete'` for a run that may have genuinely finished successfully, with nothing to ever re-observe the
  vanished workflow's real outcome. This is a strictly harder version of the already-deferred
  reconciliation-sweep gap (Non-Goals) — a `LIST`-based reconciliation pass keyed on
  `pipeline-run-id`/`batch-index`/`environment` labels is the real fix, not something this by-name-`GET`
  phase can close. Recorded here rather than solved; operationally mitigated the same way as the
  all-404'd case: keep the poll interval well under the TTL. Filed as
  [bloom #706](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/706).
- **Unbounded candidate-list growth** (found `/review-pr` round 2): `_fetch_candidate_runs` has no
  `LIMIT`/pagination — every currently-`'submitted'`/`'running'`/`'partial'` run across the whole
  deployment is re-swept, and re-`GET`'s every one of its distinct workflow names, on every cycle. At
  today's low submission volume this is negligible; revisit alongside the existing "N `GET`s per run per
  cycle" risk above if real usage grows enough to make full-table candidate scans or per-cycle K8s API
  call volume a real cost.
- **Concurrent poller replicas could flap a `'partial'`/`'running'` value between two barely-different
  snapshots** (found `/review-pr` round 2): the Python-rollup decision above already accepts that two
  overlapping cycles (single replica) racing to write is harmless (idempotent recompute, phases move
  monotonically). Multiple *replicas* polling the same run concurrently is a materially different case
  only in volume, not in kind — each replica's write is still an idempotent recompute from its own
  `get_workflow_status` snapshot, so a genuine flap would require Argo itself to report inconsistent
  phases for the same workflow to two near-simultaneous callers, not a defect in this poller's own logic.
  Not relevant at today's single-replica deployment (see the matching Open Question below); recorded here
  since round-2 review raised it as a hypothetical worth having on record before it's ever load-bearing.

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
