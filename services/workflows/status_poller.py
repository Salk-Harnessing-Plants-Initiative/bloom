"""
Pipeline status poller (bloom #11 Phase 3).

Periodically re-checks every `cyl_pipeline_runs` row still `'submitted'`/
`'running'`/`'partial'`, fetches the real Argo Workflow phase for each of
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
polled again to fix them otherwise (see design.md's Decision 6).
Distinct from `dispatch_worker.py`: that worker reacts to new pgmq messages
(event-driven); this poller runs on a fixed wall-clock cadence regardless of
dispatch activity, sweeping every currently-active run. Runs as the
least-privilege bloom_workflows app user — no direct DB connection.

Deploy: a container off the workflows image with `command: python
status_poller.py`.

Env:
    WORKFLOWS_STATUS_POLL_SECONDS  sleep between sweep cycles (default 15)
"""

import logging
import os
import signal
import time

from postgrest import APIError

from k8s_client import get_workflow_status
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
    anything already 'complete'/'failed' is fully terminal."""
    return (
        client.table("cyl_pipeline_runs")
        .select("id, status")
        .in_("status", ["submitted", "running", "partial"])
        .execute()
        .data
        or []
    )


def _fetch_effective_phases(
    client, run_id
) -> tuple[list[str], bool, int, int, list[str]]:
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
    by sweep_once as the backstop reconciliation list once this run's
    rollup concludes a terminal status — a scan can otherwise stay
    'queued' forever if write-back never ran at all for it."""
    rows = (
        client.table("cyl_pipeline_run_scans")
        .select("argo_workflow_name, status")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )

    phases = []
    if any(
        r.get("argo_workflow_name") is None and r.get("status") == "failed"
        for r in rows
    ):
        phases.append("Failed")

    workflow_names = sorted(
        {r["argo_workflow_name"] for r in rows if r.get("argo_workflow_name")}
    )
    any_unknown = False
    for name in workflow_names:
        phase = get_workflow_status(name)
        if phase is None:
            any_unknown = True
            continue
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

    return phases, any_unknown, done_count, failed_count, queued_workflow_names


def _reconcile_unresolved_scans(client, argo_workflow_name: str) -> int:
    """Close out, as 'failed', any cyl_pipeline_run_scans row for
    argo_workflow_name still 'queued' — the backstop for a scan whose
    write-back step never ran at all (fix-cyl-pipeline-run-scan-status
    round 2; see design.md's Decision 6). Called by sweep_once only once a
    run's rollup has concluded a terminal (non-'running') status, since a
    run that never polls again has no other remaining chance to resolve
    such a scan. Returns the number of rows marked failed."""
    result = (
        client.rpc(
            "fail_cyl_pipeline_run_scans_without_result",
            {
                "p_argo_workflow_name": argo_workflow_name,
                "p_error_message": (
                    "workflow reached a terminal status before write-back "
                    "produced a result for this scan"
                ),
            },
        )
        .execute()
        .data
    )
    return result or 0


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
            (
                phases,
                any_unknown,
                done_count,
                failed_count,
                queued_workflow_names,
            ) = _fetch_effective_phases(client, run_id)
        except Exception as exc:
            logger.warning(
                "status_poller: run %s status check failed, leaving unsettled "
                "for the next cycle: %s",
                run_id,
                exc,
            )
            ok = False
            continue

        status = rollup(phases)
        if status is None:
            continue
        if status == "complete" and any_unknown:
            logger.warning(
                "status_poller: run %s has an unresolved (404'd) workflow "
                "this cycle — withholding 'complete' rather than concluding "
                "it from incomplete information",
                run_id,
            )
            continue

        # Backstop reconciliation (fix-cyl-pipeline-run-scan-status round 2 —
        # see design.md's Decision 6): once this run's rollup has concluded
        # anything other than 'running', it will not be checked again after
        # this cycle's status write (a terminal write drops it from
        # _fetch_candidate_runs's candidate set for good). Any scan row still
        # 'queued' at that point can only mean write-back never ran for it at
        # all (its workflow failed before reaching write-back, or the
        # write-back container never started) — nothing else will ever
        # resolve it. Reconcile BEFORE writing the status, not after: if
        # reconciliation itself fails, skip the status write entirely this
        # cycle so the run stays a candidate and is retried next cycle,
        # matching this loop's existing per-run isolation discipline.
        if status != "running" and queued_workflow_names:
            try:
                for name in queued_workflow_names:
                    failed_count += _reconcile_unresolved_scans(client, name)
            except Exception as exc:
                logger.warning(
                    "status_poller: run %s failed to reconcile unresolved "
                    "scans before writing terminal status %s, leaving "
                    "unsettled for the next cycle: %s",
                    run_id,
                    status,
                    exc,
                )
                ok = False
                continue

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
    logger.info("status poller started (poll=%ss)", POLL_INTERVAL)
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
