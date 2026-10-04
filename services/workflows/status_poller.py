"""
Pipeline status poller (bloom #11 Phase 3).

Periodically re-checks every `cyl_pipeline_runs` row still `'submitted'`/
`'running'`, or `'partial'` and not yet concluded by this poller
(`poller_concluded_at IS NULL`), fetches the real Argo Workflow phase for each of
that run's distinct `argo_workflow_name`s via k8s_client.get_workflow_status,
computes the run's rollup status and per-scan `done_count`/`failed_count`
(see the rollup rule below), and writes them via the
`update_cyl_pipeline_run_status` SECURITY DEFINER RPC — every cycle a
candidate run has scan rows to check, even when the computed status matches
the run's already-known status, since `done_count`/`failed_count` can
advance between cycles while the overall status does not (see design.md's
Decision 3). Before writing a run's status whenever the computed conclusion
is anything other than `'running'`, this poller also reconciles — via
`fail_cyl_pipeline_run_scans_without_result` — any of that run's scans still
`'queued'`, since a run whose status write just went terminal will never be
polled again to fix them otherwise (see design.md's Decision 6). While the
run is still `'running'`, it also reconciles the `'queued'` scans of each workflow
whose own Argo phase is Succeeded/Failed/Error, so they don't wait for the run's
slowest workflow (fix-cyl-writeback-retry-reconcile, bloom #1034).
Argo deletes a finished Workflow WORKFLOWS_K8S_TTL_SECONDS after it ends, so this
poller records each workflow's terminal phase in `cyl_pipeline_run_workflows` and
falls back to it on a verified NotFound; a workflow it never saw finish is
"removed" only after a guarded wait, and while any workflow is unresolved no
terminal status is written (fix-cyl-poller-unconcluded-runs, bloom #1042).
Distinct from `dispatch_worker.py`: that worker reacts to new pgmq messages
(event-driven); this poller runs on a fixed wall-clock cadence regardless of
dispatch activity, sweeping every currently-active run. Runs as the
least-privilege bloom_workflows app user — no direct DB connection.

Deploy: a container off the workflows image with `command: python
status_poller.py`.

Env:
    WORKFLOWS_STATUS_POLL_SECONDS      sleep between sweep cycles (default 15)
    WORKFLOWS_NOT_FOUND_GRACE_SECONDS  how long a never-seen workflow must keep
                                       returning a verified NotFound before it is
                                       removed (default 600)
    WORKFLOWS_K8S_TTL_SECONDS          read through k8s_client.TTL_SECONDS: a
                                       workflow is never removed sooner than this
                                       after its newest scan row was created
"""

import datetime
import logging
import os
import signal
import time
from typing import NamedTuple

from postgrest import APIError

from k8s_client import TTL_SECONDS, get_workflow_status
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client

# PostgREST's "function/signature not found in schema cache" code — expected,
# transient, and self-healing during the brief window between an application
# deploy and its accompanying migration actually applying (this repo's
# deploy.yml applies app code before migrations; see design.md's
# fix-cyl-pipeline-run-scan-status deploy-ordering risk). Distinguished from a
# generic isolated error so a burst of these during that window doesn't also
# trigger an unnecessary proactive reconnect.
_SIGNATURE_NOT_FOUND_CODE = "PGRST202"

# A workflow in one of these phases has finished every node, write-back's
# retries included, so it can write no further result
# (fix-cyl-writeback-retry-reconcile, bloom #1034).
_TERMINAL_WORKFLOW_PHASES = frozenset({"Succeeded", "Failed", "Error"})

# The error_message on a row the poller closes. The web app's failure-hints.ts
# keeps byte-equal copies (checked by its tests).
_BACKSTOP_MESSAGE = (
    "write-back recorded no result for this scan before its "
    "workflow ended; check whether a result file exists before "
    "re-running prediction"
)
_REMOVED_MESSAGE = (
    "the workflow was removed before Bloom saw it finish; check whether a "
    "result file exists before re-running prediction"
)

# A never-seen workflow is removed only after this many consecutive verified
# NotFound lookups (as well as the grace period and the TTL bound).
_NOT_FOUND_MIN_CYCLES = 3

# Clock seams, so tests never sleep.
_monotonic = time.monotonic


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def app_client():
    """This poller's RPC/reads are all small, single-row/single-run,
    indexed operations — safe to bound tighter than supabase-py's 120s
    default (see supabase_client.py's SINGLE_ROW_RPC_TIMEOUT_SECONDS)."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


logger = logging.getLogger(__name__)


def _resolve_poll_interval() -> float:
    """Never raises — a present-but-malformed value must degrade to the safe
    default, not crash the module at import time, matching k8s_client.py's
    _resolve_ttl_seconds() convention for the same class of failure mode.
    Also rejects a parseable-but-non-positive value (found during
    /review-pr round 5): a negative number parses fine as a float, but
    time.sleep() raises ValueError for a negative argument — an uncaught
    crash at every one of run()'s three sleep call sites, undermining this
    function's own "never raises" guarantee. Zero parses and doesn't crash
    time.sleep(), but removes the only throttle on the sweep loop entirely.
    Both fall back to the same safe default as a malformed string, with a
    warning logged so the misconfiguration is operator-visible rather than
    silently ignored forever."""
    raw = os.environ.get("WORKFLOWS_STATUS_POLL_SECONDS", "15")
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "status_poller: WORKFLOWS_STATUS_POLL_SECONDS=%r is not a valid "
            "number, falling back to the default of 15s",
            raw,
        )
        return 15.0
    if value <= 0:
        logger.warning(
            "status_poller: WORKFLOWS_STATUS_POLL_SECONDS=%r is not a "
            "positive number, falling back to the default of 15s",
            raw,
        )
        return 15.0
    return value


POLL_INTERVAL = _resolve_poll_interval()


def _resolve_not_found_grace() -> float:
    """WORKFLOWS_NOT_FOUND_GRACE_SECONDS, never raising: a malformed or
    non-positive value falls back to 600 with a warning, as _resolve_poll_interval
    does."""
    raw = os.environ.get("WORKFLOWS_NOT_FOUND_GRACE_SECONDS", "600")
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value <= 0:
        logger.warning(
            "status_poller: WORKFLOWS_NOT_FOUND_GRACE_SECONDS=%r is not a positive "
            "number, falling back to the default of 600s",
            raw,
        )
        return 600.0
    return value


NOT_FOUND_GRACE_SECONDS = _resolve_not_found_grace()


def _warn_if_removal_disabled() -> None:
    """A workflow cannot be garbage-collected sooner than the TTL after its rows
    were created, which is the guard that keeps a misconfigured namespace from
    failing a young run. With a TTL that isn't positive the guard means nothing,
    so removal is switched off."""
    if TTL_SECONDS <= 0:
        logger.warning(
            "status_poller: WORKFLOWS_K8S_TTL_SECONDS=%s is not positive; workflows "
            "that 404 before this poller sees them finish will never be removed",
            TTL_SECONDS,
        )


class _NotFoundTracker:
    """Consecutive verified-NotFound lookups per (run_id, argo_workflow_name),
    held in memory: a restart only delays a removal by one grace period. Each
    replica counts on its own."""

    def __init__(self):
        self._entries: dict[tuple, tuple[int, float]] = {}
        self._seen: set[tuple] = set()

    def clear(self):
        self._entries.clear()
        self._seen.clear()

    def observe(self, key) -> tuple[int, float]:
        """Count one more verified NotFound for `key`; return (count, first_seen)."""
        self._seen.add(key)
        count, first = self._entries.get(key, (0, _monotonic()))
        self._entries[key] = (count + 1, first)
        return self._entries[key]

    def reset(self, key):
        self._seen.add(key)
        self._entries.pop(key, None)

    def reset_run(self, run_id):
        for key in [k for k in self._entries if k[0] == run_id]:
            del self._entries[key]

    def count(self, key) -> int:
        return self._entries.get(key, (0, 0.0))[0]

    def prune(self):
        """Drop every pair not looked up since the last prune."""
        for key in [k for k in self._entries if k not in self._seen]:
            del self._entries[key]
        self._seen = set()


_not_found = _NotFoundTracker()


def _parse_timestamp(value) -> datetime.datetime | None:
    """A PostgREST timestamptz string as an aware datetime, or None when it is
    missing, unparseable or has no offset (then the TTL guard can't be met)."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# Number of consecutive unclean sweep cycles (see sweep_once's return value)
# before run() proactively reconnects rather than continuing to reuse a
# client whose session may have genuinely died — see design.md's "run()
# reconnects after consecutive error cycles" decision (/review-pr round 2).
_MAX_CONSECUTIVE_ERROR_CYCLES = 3

_running = True


def _stop(signum, _frame):
    """SIGTERM/SIGINT -> flip the running-flag only. Does not interrupt an
    in-flight sweep_once() call, so a run's in-flight check/update always
    finishes; the loop simply doesn't start a new sweep cycle after."""
    global _running
    logger.info(
        "status_poller: received signal %s, shutting down after current sweep",
        signum,
    )
    _running = False


def rollup(effective_phases: list[str]) -> str | None:
    """Pure rollup rule (cyl-pipeline-status-polling spec's "Rollup rule..."
    requirement): rule (0) an empty list concludes nothing (None) — checked
    first so it can never vacuously satisfy rule (2)'s all()-over-Succeeded.
    Otherwise: (1) any Pending/Running -> 'running'; (2) all Succeeded ->
    'complete'; (3) none Succeeded -> 'failed'; (4) a mix -> 'partial'."""
    if not effective_phases:
        return None
    if any(p in ("Pending", "Running") for p in effective_phases):
        return "running"
    if all(p == "Succeeded" for p in effective_phases):
        return "complete"
    if not any(p == "Succeeded" for p in effective_phases):
        return "failed"
    return "partial"


def _fetch_candidate_runs(client) -> list[dict]:
    """Every run still eligible for real-outcome polling — dispatch already
    fully or partially succeeded ('submitted'), a prior sweep already started
    progressing it ('running'), or Phase 2 settled it to 'partial' (some
    scans failed to dispatch, but others may still have genuinely-dispatched
    batches whose real Argo outcome hasn't been checked yet — see design.md's
    "'partial' runs are included in the polling candidate set" decision,
    found during /review-pr round 1). A 'queued' run was never dispatched;
    anything already 'complete'/'failed' is fully terminal, and so is a
    'partial' this poller concluded (poller_concluded_at set,
    fix-cyl-poller-unconcluded-runs). That filter is applied here rather than in
    the query: PostgREST would need a nested or=(...,and(...)) for it."""
    rows = (
        client.table("cyl_pipeline_runs")
        .select("id, status, poller_concluded_at")
        .in_("status", ["submitted", "running", "partial"])
        .execute()
        .data
        or []
    )
    return [
        r
        for r in rows
        if not (r.get("status") == "partial" and r.get("poller_concluded_at"))
    ]


class EffectivePhases(NamedTuple):
    """_fetch_effective_phases's result; see its docstring for each field."""

    phases: list[str]
    any_unknown: bool
    done_count: int
    failed_count: int
    queued_workflow_names: list[str]
    settled_workflow_names: list[str]
    # False when a phase record or a removed workflow's close-out failed this
    # cycle (not PGRST202), so the cycle counts as unclean.
    clean: bool = True


def _fetch_effective_phases(client, run_id) -> EffectivePhases:
    """One run's effective-phase list: 'Failed' for each scan whose dispatch
    itself failed (status='failed', argo_workflow_name IS NULL), plus the
    real Argo phase of each distinct argo_workflow_name among the run's
    scans. Also returns any_unknown: True if any workflow this cycle
    returned None (404) from get_workflow_status and was excluded from
    phases rather than guessed. sweep_once uses any_unknown to withhold a
    'complete' conclusion when the evidence is incomplete (found during
    /review-pr round 1 — see design.md's "a partial 404 must not let the
    rollup conclude 'complete'" decision). A K8sConfigError/K8sStatusError
    from get_workflow_status propagates to the caller, which is responsible
    for leaving this run unsettled and moving on to the next candidate.

    Also returns done_count/failed_count (bloom #716,
    fix-cyl-pipeline-run-scan-status): counted from the SAME `rows` fetch
    above rather than a second query — done_count is the number of this
    run's scans with status IN ('written', 'reused') (a real per-scan
    pipeline success — 'reused' stays included for forward compatibility
    with the separate, still-unimplemented pre-dispatch skip-if-done
    mechanism, even though nothing in this program currently produces it);
    failed_count is the number with status = 'failed' (real per-scan
    failure OR a dispatch-level failure — both mean "this scan produced no
    useful result").

    Also returns queued_workflow_names (fix-cyl-pipeline-run-scan-status
    round 2 — see design.md's Decision 6): the sorted, distinct
    argo_workflow_names among this run's rows still status = 'queued'. Used
    by sweep_once as the backstop reconciliation list — a scan can otherwise
    stay 'queued' forever if write-back never ran at all for it, or if
    write-back's final attempt still had a retriable envelope failure (bloomctl
    then deliberately leaves the workflow's rows to this poller, bloom #1034).

    Also returns settled_workflow_names (fix-cyl-writeback-retry-reconcile,
    bloom #1034): the queued_workflow_names whose own phase this cycle is
    Succeeded/Failed/Error, so they can write nothing more. A 404 is never
    settled: a 404 can also come from a misconfigured namespace, API URL or CRD
    while the workflow is still running, so those rows wait for the
    terminal-rollup backstop."""
    rows = _fetch_run_rows(client, run_id)
    stored = _fetch_stored_phases(client, run_id)
    workflow_names = sorted(
        {r["argo_workflow_name"] for r in rows if r.get("argo_workflow_name")}
    )

    clean = True
    live: dict[str, str] = {}
    gone: list[str] = []
    for name in workflow_names:
        phase = get_workflow_status(name, run_id=run_id)
        if phase is None:
            gone.append(name)
            continue
        _not_found.reset((run_id, name))
        live[name] = phase
        if phase in _TERMINAL_WORKFLOW_PHASES and stored.get(name) != phase:
            clean = _record_phase_safely(client, run_id, name, phase) and clean

    removed: list[str] = []
    closed_any = False
    for name in gone:
        count, first_seen = _not_found.observe((run_id, name))
        if name in stored or not _is_removed(name, rows, count, first_seen):
            continue
        if any(
            r.get("argo_workflow_name") == name and r.get("status") == "queued"
            for r in rows
        ):
            closed, ok = _close_removed_workflow(client, run_id, name)
            clean = clean and ok
            if closed is None:
                continue
            closed_any = True
        removed.append(name)
    if closed_any:
        rows = _fetch_run_rows(client, run_id)

    phases = []
    if any(
        r.get("argo_workflow_name") is None and r.get("status") == "failed"
        for r in rows
    ):
        phases.append("Failed")

    any_unknown = False
    workflow_phases: dict[str, str] = {}
    for name in workflow_names:
        if name in live:
            phase = live[name]
        elif name in stored:
            phase = stored[name]
        elif name in removed:
            phase = (
                "Succeeded"
                if all(
                    r.get("status") in ("written", "reused")
                    for r in rows
                    if r.get("argo_workflow_name") == name
                )
                else "Failed"
            )
        else:
            any_unknown = True
            continue
        workflow_phases[name] = phase
        phases.append(phase)

    done_count = sum(1 for r in rows if r.get("status") in ("written", "reused"))
    failed_count = sum(1 for r in rows if r.get("status") == "failed")
    queued_workflow_names = sorted(
        {
            r["argo_workflow_name"]
            for r in rows
            if r.get("status") == "queued" and r.get("argo_workflow_name")
        }
    )

    settled_workflow_names = [
        name
        for name in queued_workflow_names
        if workflow_phases.get(name) in _TERMINAL_WORKFLOW_PHASES
    ]

    return EffectivePhases(
        phases,
        any_unknown,
        done_count,
        failed_count,
        queued_workflow_names,
        settled_workflow_names,
        clean,
    )


def _fetch_run_rows(client, run_id) -> list[dict]:
    return (
        client.table("cyl_pipeline_run_scans")
        .select("argo_workflow_name, status, created_at")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )


def _fetch_stored_phases(client, run_id) -> dict[str, str]:
    """The last terminal phase this poller recorded for each of run_id's
    workflows (cyl_pipeline_run_workflows)."""
    rows = (
        client.table("cyl_pipeline_run_workflows")
        .select("argo_workflow_name, phase")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )
    return {r["argo_workflow_name"]: r["phase"] for r in rows}


def _record_phase(client, run_id, argo_workflow_name: str, phase: str) -> bool:
    return bool(
        client.rpc(
            "record_cyl_pipeline_workflow_phase",
            {
                "p_run_id": run_id,
                "p_argo_workflow_name": argo_workflow_name,
                "p_phase": phase,
            },
        )
        .execute()
        .data
    )


def _record_phase_safely(client, run_id, name: str, phase: str) -> bool:
    """Record a live terminal phase. A failure doesn't change this cycle's
    effective phase (the live one is used) and is retried next cycle while the
    workflow still exists. Returns whether the cycle stays clean."""
    try:
        _record_phase(client, run_id, name, phase)
    except Exception as exc:
        if isinstance(exc, APIError) and exc.code == _SIGNATURE_NOT_FOUND_CODE:
            logger.info(
                "status_poller: run %s phase record deferred — RPC signature not "
                "yet migrated: %s",
                run_id,
                exc,
            )
            return True
        logger.warning(
            "status_poller: run %s failed to record %s's phase %s, will retry: %s",
            run_id,
            name,
            phase,
            exc,
        )
        return False
    return True


def _is_removed(name: str, rows: list[dict], count: int, first_seen: float) -> bool:
    """A verified-NotFound workflow with no stored phase counts as removed once
    it has been NotFound on _NOT_FOUND_MIN_CYCLES consecutive lookups spanning
    NOT_FOUND_GRACE_SECONDS, and its newest scan row is at least TTL_SECONDS old:
    rows are created before dispatch, and Argo deletes a Workflow TTL after it
    ends, so a NotFound sooner than that is not garbage collection."""
    if TTL_SECONDS <= 0:
        return False
    if count < _NOT_FOUND_MIN_CYCLES:
        return False
    if _monotonic() - first_seen < NOT_FOUND_GRACE_SECONDS:
        return False
    stamps = [
        _parse_timestamp(r.get("created_at"))
        for r in rows
        if r.get("argo_workflow_name") == name
    ]
    if not stamps or any(s is None for s in stamps):
        return False
    newest = max(stamps)
    return (_utcnow() - newest).total_seconds() >= TTL_SECONDS


def _close_removed_workflow(client, run_id, name: str) -> tuple[int | None, bool]:
    """Close a removed workflow's 'queued' rows with the removed message.
    Returns (rows closed, or None on failure; whether the cycle stays clean).
    On failure the workflow stays unresolved this cycle, so the run can't
    conclude with rows still queued."""
    try:
        closed = _reconcile_unresolved_scans(client, run_id, name, _REMOVED_MESSAGE)
    except Exception as exc:
        if isinstance(exc, APIError) and exc.code == _SIGNATURE_NOT_FOUND_CODE:
            logger.info(
                "status_poller: run %s close-out of removed workflow %s deferred — "
                "RPC signature not yet migrated: %s",
                run_id,
                name,
                exc,
            )
            return None, True
        logger.warning(
            "status_poller: run %s failed to close out removed workflow %s, will "
            "retry: %s",
            run_id,
            name,
            exc,
        )
        return None, False
    logger.warning(
        "status_poller: run %s workflow %s was removed before this poller saw it "
        "finish; closed %s 'queued' scan(s)",
        run_id,
        name,
        closed,
    )
    return closed, True


def _reconcile_unresolved_scans(
    client, run_id, argo_workflow_name: str, message: str = _BACKSTOP_MESSAGE
) -> int:
    """Close out, as 'failed', run_id's cyl_pipeline_run_scans rows for
    argo_workflow_name still 'queued', through the run-scoped
    close_cyl_pipeline_run_workflow_scans: a garbage-collected workflow's
    generated name can be reused by a later run, so the name-only RPC bloomctl
    uses is not safe here (fix-cyl-poller-unconcluded-runs). The backstop for a
    scan whose write-back step never ran at all (fix-cyl-pipeline-run-scan-status
    round 2; see design.md's Decision 6), or one whose write-back step's final
    attempt still had a retriable envelope failure, which bloomctl leaves for this
    poller (bloom #1034). Called by sweep_once for a workflow whose phase (live or
    stored) is terminal while its run is still 'running', for every workflow once
    the run's rollup has concluded a non-'running' status, and, with
    _REMOVED_MESSAGE, for a removed workflow. Returns the number of rows closed."""
    result = (
        client.rpc(
            "close_cyl_pipeline_run_workflow_scans",
            {
                "p_run_id": run_id,
                "p_argo_workflow_name": argo_workflow_name,
                "p_error_message": message,
            },
        )
        .execute()
        .data
    )
    return result or 0


def _count_done_and_failed(client, run_id) -> tuple[int, int]:
    """Fresh done_count/failed_count for run_id, from a plain re-read of
    cyl_pipeline_run_scans — deliberately independent of _fetch_effective_phases's
    phases/K8s lookups (fix-cyl-pipeline-run-scan-status round 2, found during
    /review-pr round 2's second pass). _fetch_effective_phases's own counts are a
    snapshot taken before the reconciliation RPC (and before this cycle's K8s
    lookups) even run; if a scan's write-back genuinely resolves ('queued' ->
    'written') in that window, the reconciliation RPC correctly leaves it alone
    (its own WHERE status='queued' guard no longer matches) — but incrementing
    the STALE snapshot's failed_count by the reconciled count would never credit
    that scan's completion to done_count either, permanently undercounting a run
    that then goes terminal and is never revisited. sweep_once calls this for a
    fresh recount immediately after reconciling, rather than reusing the earlier
    snapshot."""
    rows = (
        client.table("cyl_pipeline_run_scans")
        .select("status")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )
    done_count = sum(1 for r in rows if r.get("status") in ("written", "reused"))
    failed_count = sum(1 for r in rows if r.get("status") == "failed")
    return done_count, failed_count


def _close_out_workflows(
    client, run_id, names: list[str], context: str
) -> tuple[tuple[int, int] | None, bool]:
    """Reconcile each of `names`' still-'queued' rows, then recount the run.
    `context` says why, for the logs.

    Returns (counts, clean):
      - success: (fresh (done_count, failed_count), True)
      - PGRST202, the RPC not yet migrated (design.md's Decision 6 addendum 7):
        (None, True), expected and transient, so the cycle stays clean
      - any other reconcile or recount failure: (None, False)
    The counts are re-derived fresh rather than by incrementing the snapshot
    _fetch_effective_phases returned, which predates this cycle's K8s lookups
    and this reconciliation and can go stale (see _count_done_and_failed)."""
    try:
        for name in names:
            closed = _reconcile_unresolved_scans(client, run_id, name)
            if closed:
                logger.info(
                    "status_poller: run %s closed %s 'queued' scan(s) of %s (%s)",
                    run_id,
                    closed,
                    name,
                    context,
                )
    except Exception as exc:
        if isinstance(exc, APIError) and exc.code == _SIGNATURE_NOT_FOUND_CODE:
            logger.info(
                "status_poller: run %s reconciliation deferred — RPC "
                "signature not yet migrated (expected transient "
                "deploy-ordering window): %s",
                run_id,
                exc,
            )
            return None, True
        logger.warning(
            "status_poller: run %s failed to reconcile unresolved scans (%s), "
            "leaving them for the next cycle: %s",
            run_id,
            context,
            exc,
        )
        return None, False
    try:
        return _count_done_and_failed(client, run_id), True
    except Exception as exc:
        logger.warning(
            "status_poller: run %s failed to recount scans after reconciling "
            "(%s), leaving them for the next cycle: %s",
            run_id,
            context,
            exc,
        )
        return None, False


def update_run_status(
    client,
    run_id,
    status: str,
    done_count: int | None = None,
    failed_count: int | None = None,
) -> None:
    client.rpc(
        "update_cyl_pipeline_run_status",
        {
            "p_run_id": run_id,
            "p_status": status,
            "p_done_count": done_count,
            "p_failed_count": failed_count,
        },
    ).execute()


def sweep_once(client) -> bool:
    """One full cycle: check every candidate run, updating those with a
    concluded rollup status. A failure checking or updating one run (a K8s
    error, a DB-read error, or a lost RPC write) is isolated to that run — it
    must not abort the cycle for the rest of the candidates. A failure
    fetching the candidate list itself is isolated to this cycle — the next
    scheduled cycle retries rather than crashing the process (found during
    /review-pr round 1 — see design.md's "DB-read failures inside a sweep
    are isolated" decision).

    Returns True if the cycle completed with no isolated errors, False
    otherwise — run() uses this to detect a possibly-dead client session and
    force a reconnect after enough consecutive unclean cycles, since an
    isolated error caught here no longer propagates to trigger run()'s own
    exception-based reconnect the way it did before per-run isolation existed
    (found during /review-pr round 2 — see design.md's "run() reconnects
    after consecutive error cycles" decision)."""
    ok = True
    try:
        candidates = _fetch_candidate_runs(client)
    except Exception as exc:
        logger.warning(
            "status_poller: failed to fetch candidate runs this cycle, will "
            "retry next cycle: %s",
            exc,
        )
        return False

    for run in candidates:
        run_id = run["id"]
        try:
            fetched = EffectivePhases(*_fetch_effective_phases(client, run_id))
        except Exception as exc:
            logger.warning(
                "status_poller: run %s status check failed, leaving unsettled "
                "for the next cycle: %s",
                run_id,
                exc,
            )
            # None of this run's workflows got a clean lookup this cycle, so no
            # NotFound streak of its may carry over (fix-cyl-poller-unconcluded-runs).
            _not_found.reset_run(run_id)
            ok = False
            continue
        (
            phases,
            any_unknown,
            done_count,
            failed_count,
            queued_workflow_names,
            settled_workflow_names,
            fetched_clean,
        ) = fetched
        ok = ok and fetched_clean

        status = rollup(phases)
        if status is None:
            continue
        if status != "running" and any_unknown:
            # Every terminal conclusion waits while a workflow is unresolved (a
            # verified NotFound with no stored phase, not yet removed): a NotFound
            # inside the grace period can come from a misconfigured namespace
            # while that workflow still runs, and a terminal write is final. The
            # removal rule in _fetch_effective_phases bounds the wait
            # (fix-cyl-poller-unconcluded-runs, design D5).
            logger.warning(
                "status_poller: run %s has an unresolved workflow this cycle — "
                "withholding %r until it is resolved or removed",
                run_id,
                status,
            )
            continue

        if status == "running":
            # Close out each workflow whose own phase is confirmed terminal
            # (fix-cyl-writeback-retry-reconcile, bloom #1034): bloomctl leaves
            # a workflow's rows to this poller when write-back's last attempt
            # had a retriable envelope failure, and a run's 25-scan workflows
            # can finish hours apart. A live or stored terminal phase counts; an
            # unresolved workflow's does not (a misconfigured namespace also
            # returns NotFound while the workflow still runs), so those rows wait
            # for the removal rule.
            # Write progress whether or not this succeeded: the run stays a
            # candidate either way, so skipping the write would only freeze its
            # counts; a failed close-out already marked the cycle unclean.
            if settled_workflow_names:
                counts, clean = _close_out_workflows(
                    client,
                    run_id,
                    settled_workflow_names,
                    "terminal workflows of a running run",
                )
                ok = ok and clean
                if counts is not None:
                    done_count, failed_count = counts
        elif queued_workflow_names:
            # Backstop reconciliation (fix-cyl-pipeline-run-scan-status round 2
            # — see design.md's Decision 6): once this run's rollup has
            # concluded anything other than 'running', it will not be checked
            # again after this cycle's status write (a terminal write drops it
            # from _fetch_candidate_runs's candidate set for good). Any scan row
            # still 'queued' at that point means write-back never ran for it at
            # all (its workflow failed before reaching write-back, or the
            # write-back container never started), or write-back's final
            # attempt still had a retriable envelope failure and bloomctl left
            # it to this poller (bloom #1034) — nothing else will ever resolve
            # it. Reconcile BEFORE writing the status, not after: if
            # reconciliation itself fails, skip the status write entirely this
            # cycle so the run stays a candidate and is retried next cycle,
            # matching this loop's existing per-run isolation discipline.
            #
            # This branch is reached only when no workflow is unresolved (the
            # withhold above), so every workflow whose rows it closes has a live,
            # stored or removed phase. Addendum 8 of
            # fix-cyl-pipeline-run-scan-status kept this backstop ungated because
            # a GC'd sibling's 404 never cleared; the stored phase and the removal
            # rule now resolve that sibling instead.
            counts, clean = _close_out_workflows(
                client,
                run_id,
                queued_workflow_names,
                f"before writing terminal status {status}",
            )
            ok = ok and clean
            if counts is None:
                continue
            done_count, failed_count = counts

        # No same-value skip for 'running' anymore (removed by
        # fix-cyl-pipeline-run-scan-status): done_count/failed_count can
        # advance every cycle even while the overall status doesn't, so the
        # write must happen every cycle a candidate run reaches this point —
        # skipping it would freeze the "N/M scans done" progress display at
        # whatever it read on the run's first 'running' cycle.
        try:
            update_run_status(client, run_id, status, done_count, failed_count)
        except APIError as exc:
            if exc.code == _SIGNATURE_NOT_FOUND_CODE:
                # Expected during the brief window between this deploy's app
                # code going live and its migration actually applying — log
                # quietly and do NOT mark the cycle unclean, so a burst of
                # these doesn't also trigger run()'s proactive reconnect.
                logger.info(
                    "status_poller: run %s update to %s deferred — RPC "
                    "signature not yet migrated (expected transient "
                    "deploy-ordering window): %s",
                    run_id,
                    status,
                    exc,
                )
            else:
                logger.error(
                    "status_poller: run %s update to %s failed, will retry "
                    "next cycle: %s",
                    run_id,
                    status,
                    exc,
                )
                ok = False
            continue
        except Exception as exc:
            logger.error(
                "status_poller: run %s update to %s failed, will retry next cycle: %s",
                run_id,
                status,
                exc,
            )
            ok = False
            continue

        logger.info("status_poller: run %s -> %s", run_id, status)

    _not_found.prune()
    return ok


def _connect_with_retry():
    """Retry app_client() with backoff until it succeeds or a shutdown signal
    arrives. A transient Supabase outage at container startup must not crash
    the process — Docker's `restart: unless-stopped` would just crash-loop it
    forever, unlike the in-loop reconnect below, which already retries."""
    client = None
    while client is None and _running:
        try:
            client = app_client()
        except Exception as exc:
            logger.error(
                "status_poller: could not connect on startup, retrying in %ss: %s",
                POLL_INTERVAL,
                exc,
            )
            time.sleep(POLL_INTERVAL)
    return client


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = _connect_with_retry()
    if client is None:
        logger.info("status poller stopped before connecting")
        return
    logger.info(
        "status poller started (poll=%ss, not-found grace=%ss, ttl=%ss)",
        POLL_INTERVAL,
        NOT_FOUND_GRACE_SECONDS,
        TTL_SECONDS,
    )
    _warn_if_removal_disabled()
    consecutive_error_cycles = 0
    while _running:
        try:
            clean = sweep_once(client)
        except Exception as exc:
            # A genuinely-unexpected bug inside sweep_once's own control flow
            # (not one of the per-run/per-cycle errors sweep_once already
            # catches and reports via its return value) — reconnect and retry.
            logger.exception("status_poller: sweep error, reconnecting: %s", exc)
            time.sleep(POLL_INTERVAL)
            try:
                client = app_client()
                # Only reset the streak on an actual successful reconnect —
                # matching the proactive-reconnect path below. Found during
                # /review-pr round 3: this used to reset unconditionally,
                # even when the reconnect attempt itself failed, which was
                # inconsistent (if behaviorally benign, since this path
                # already retries app_client() every cycle regardless).
                consecutive_error_cycles = 0
            except Exception as reconnect_exc:
                logger.error(
                    "status_poller: reconnect failed, will retry: %s",
                    reconnect_exc,
                )
            continue

        if clean:
            consecutive_error_cycles = 0
        else:
            consecutive_error_cycles += 1
            if consecutive_error_cycles >= _MAX_CONSECUTIVE_ERROR_CYCLES:
                logger.warning(
                    "status_poller: %d consecutive sweep cycles had isolated "
                    "errors, reconnecting proactively in case the client "
                    "session is dead",
                    consecutive_error_cycles,
                )
                try:
                    client = app_client()
                    consecutive_error_cycles = 0
                except Exception as reconnect_exc:
                    logger.error(
                        "status_poller: proactive reconnect failed, will retry: %s",
                        reconnect_exc,
                    )

        if _running:
            time.sleep(POLL_INTERVAL)
    logger.info("status poller stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
