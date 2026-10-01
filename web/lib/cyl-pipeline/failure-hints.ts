/**
 * Hints for a failed run-scan row in the drill-down (design D7).
 */

import type { RunScanRow } from "./realtime-reducer";
import type { ScanMeta } from "./scan-meta";
import { stageInProblems, type StageInProblem } from "./stage-in";

/**
 * The status poller's backstop text, for a scan still `queued` when its run
 * ended (`services/workflows/status_poller.py`, `_reconcile_unresolved_scans`).
 * failure-hints.test.ts reads the poller's source to keep the two equal.
 */
export const BACKSTOP_MESSAGE = "workflow reached a terminal status before write-back produced a result for this scan";

/**
 * Write-back's own text for a scan it dispatched but never resolved
 * (bloomctl `cyl/ingest.py`, `NO_RESULT_MESSAGE`, recorded by
 * `fail_cyl_pipeline_run_scans_without_result` at the end of each batch).
 * This, not the poller's backstop, is what a #900 no-op re-delivery gets:
 * every failed row on staging runs 9-11 carries it. A real stage-in failure
 * (a poison scan) gets it too, which is why the note also needs results.
 * failure-hints.test.ts reads ingest.py to keep the two equal.
 */
export const WRITEBACK_NO_RESULT_MESSAGE = "no result produced for this scan by write-back";

export const NO_OP_NOTE =
  "This scan has pipeline results, but this row recorded none. Either its result arrived after the run closed, or, if the scan already had results before this run, this was an unrecognised no-op re-delivery, which re-running won't change (bloom#900). Check the scan's traits before re-running.";

const CAUSES: Record<StageInProblem, string> = {
  "species-missing": "species missing",
  "age-missing": "plant age missing",
  "age-not-whole": "plant age is not a whole number",
};

export function likelyCause(meta: Pick<ScanMeta, "species_name" | "plant_age_days"> | undefined): string | null {
  if (!meta) return null;
  const problems = stageInProblems(meta);
  return problems.length ? `Likely cause: ${problems.map((p) => CAUSES[p]).join("; ")}` : null;
}

/**
 * bloom#900: re-running a scan whose only source was ingested outside any run
 * is reported failed, because the redelivery fallback only matches sources a
 * run-scan row already carries; write-back then closes the row with its
 * no-result text. Narrow on purpose: the note needs one of the two no-result
 * texts exactly, and a scan that currently has pipeline results.
 * It names a second cause too: a write-back that lands after the backstop has
 * failed the row adds traits but leaves the row failed with no source_id (the
 * a9 write-back RPC's status != 'failed' guard), so the scan has results the
 * row doesn't show.
 */
export function isNoOpCandidate(row: Pick<RunScanRow, "status" | "error_message">, hasResults: boolean): boolean {
  const noResult = row.error_message === BACKSTOP_MESSAGE || row.error_message === WRITEBACK_NO_RESULT_MESSAGE;
  return row.status === "failed" && noResult && hasResults;
}
