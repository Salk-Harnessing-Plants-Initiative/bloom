"""
Pipeline status poller (bloom #11 Phase 3).

Periodically re-checks every `cyl_pipeline_runs` row still `'submitted'`/
`'running'`/`'partial'`, fetches the real Argo Workflow phase for each of
that run's distinct `argo_workflow_name`s via k8s_client.get_workflow_status,
computes the run's rollup status (see the rollup rule below), and writes it
via the `update_cyl_pipeline_run_status` SECURITY DEFINER RPC — skipping the
write entirely when the computed status already matches a known `'running'`
status, so a stable in-progress run doesn't get needlessly re-confirmed
forever (this skip does NOT apply to `'partial'`: Phase 2's dispatch-settle
can also produce `'partial'` as a pre-poll guess this poller hasn't yet
checked, so a `'partial'`-sourced candidate always writes its computed
conclusion — see design.md's round-3 fix).
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

from k8s_client import get_workflow_status
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client


def app_client():
    """This poller's RPC/reads are all small, single-row/single-run,
    indexed operations — safe to bound tighter than supabase-py's 120s
    default (see supabase_client.py's SINGLE_ROW_RPC_TIMEOUT_SECONDS)."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


logger = logging.getLogger(__name__)


def _resolve_poll_interval() -> float:
    """Never raises — a present-but-malformed value must degrade to the safe
    default, not crash the module at import time, matching k8s_client.py's
    _resolve_ttl_seconds() convention for the same class of failure mode."""
    raw = os.environ.get("WORKFLOWS_STATUS_POLL_SECONDS", "15")
    try:
        return float(raw)
    except ValueError:
        return 15.0


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
    anything already 'complete'/'failed' is fully terminal. Also selects
    `status` (not just `id`) so sweep_once can skip re-writing a conclusion
    that hasn't actually changed (found during /review-pr round 2 — see
    design.md's "repeated same-value reconfirmation no longer rewrites the
    row" decision)."""
    return (
        client.table("cyl_pipeline_runs")
        .select("id, status")
        .in_("status", ["submitted", "running", "partial"])
        .execute()
        .data
        or []
    )


def _fetch_effective_phases(client, run_id) -> tuple[list[str], bool]:
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
    for leaving this run unsettled and moving on to the next candidate."""
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

    return phases, any_unknown


def update_run_status(client, run_id, status: str) -> None:
    client.rpc(
        "update_cyl_pipeline_run_status", {"p_run_id": run_id, "p_status": status}
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
        known_status = run.get("status")
        try:
            phases, any_unknown = _fetch_effective_phases(client, run_id)
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
        if status == known_status == "running":
            # Nothing has changed since the last confirmed conclusion — skip
            # the write entirely rather than re-writing an identical value
            # for a run whose outcome has already stabilized (found during
            # /review-pr round 2). Only safe for 'running': Phase 2's
            # dispatch-settle never writes 'running' itself, so a known
            # status of 'running' unambiguously means this poller already
            # confirmed it. 'partial' is NOT eligible for this skip — Phase
            # 2's dispatch-settle *can* produce 'partial' as a pre-poll
            # guess never yet checked by this poller, so a same-string match
            # there doesn't mean "already confirmed" and would silently
            # discard the run's first real conclusion (found during
            # /review-pr round 3 — see design.md's "the same-value
            # write-skip only applies to 'running'" decision).
            logger.debug(
                "status_poller: run %s reconfirmed 'running', no change — "
                "skipping the write",
                run_id,
            )
            continue

        try:
            update_run_status(client, run_id, status)
        except Exception as exc:
            logger.error(
                "status_poller: run %s update to %s failed, will retry next "
                "cycle: %s",
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
                        "status_poller: proactive reconnect failed, will "
                        "retry: %s",
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
