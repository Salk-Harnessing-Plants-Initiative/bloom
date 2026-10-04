/**
 * Hints for a failed run-scan row in the drill-down (design D7).
 */

import type { ScanMeta } from "./scan-meta";
import { stageInProblems, type StageInProblem } from "./stage-in";

/**
 * The status poller's backstop text, for a scan still `queued` when its workflow
 * ended (`services/workflows/status_poller.py`, `_BACKSTOP_MESSAGE`).
 * failure-hints.test.ts reads the poller's source to keep the two equal.
 */
export const BACKSTOP_MESSAGE =
  "write-back recorded no result for this scan before its workflow ended; check whether a result file exists before re-running prediction";

/**
 * The status poller's text for a scan still `queued` when its workflow was
 * removed (garbage-collected) before the poller saw it finish
 * (`status_poller.py`, `_REMOVED_MESSAGE`; fix-cyl-poller-unconcluded-runs).
 * failure-hints.test.ts reads the poller's source to keep the two equal.
 */
export const REMOVED_WORKFLOW_MESSAGE =
  "the workflow was removed before Bloom saw it finish; check whether a result file exists before re-running prediction";

/**
 * Write-back's own text for a scan it dispatched but never resolved
 * (bloomctl `cyl/ingest.py`, `NO_RESULT_MESSAGE`, recorded by
 * `fail_cyl_pipeline_run_scans_without_result` at the end of a batch with no
 * retriable envelope failure),
 * for example after a stage-in failure (a poison scan). No hint reads this
 * text, BACKSTOP_MESSAGE or REMOVED_WORKFLOW_MESSAGE today; all three stay,
 * with their source-equality tests, as the named texts a failed no-result row
 * carries (fix-cyl-noop-redelivery-scan-resolution, design D4).
 * failure-hints.test.ts reads ingest.py to keep the two equal.
 */
export const WRITEBACK_NO_RESULT_MESSAGE = "no result produced for this scan by write-back";

/**
 * The dispatch worker's texts for a batch it refused before submitting
 * anything: the environment is switched off, or has no stage root or
 * credential Secret (`services/workflows/dispatch_worker.py`,
 * `_REFUSAL_MESSAGES`; bloom#863). failure-hints.test.ts reads that file to
 * keep the two equal.
 */
export const DISPATCH_REFUSED_MESSAGES: readonly string[] = [
  "Pipeline dispatch is turned off in this environment",
  "Pipeline dispatch is not configured in this environment",
];

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
 * The likely-cause hint for a failed row, or none when the dispatch worker
 * refused the batch: that scan never reached stage-in, so its metadata didn't
 * cause the failure.
 */
export function failedScanCause(
  errorMessage: string | null,
  meta: Pick<ScanMeta, "species_name" | "plant_age_days"> | undefined,
): string | null {
  if (errorMessage !== null && DISPATCH_REFUSED_MESSAGES.includes(errorMessage)) return null;
  return likelyCause(meta);
}

/**
 * A failed row whose scan's latest source this run wrote: write-back delivered
 * this run's result after the row was closed (the poller's backstop, its
 * close-out of a removed workflow, or end-of-batch reconciliation), and the RPC
 * leaves a failed row as it is. The
 * row shows no source, so this names it. Exact: a source records the run that
 * wrote it (`cyl_trait_sources.cyl_pipeline_run_id`), and a run has one row per
 * scan.
 */
export function lateResultNote(
  status: string,
  latestSourceId: number | null | undefined,
  latestSourceRunId: number | null | undefined,
  runId: number,
): string | null {
  if (status !== "failed" || latestSourceId == null || latestSourceRunId !== runId) return null;
  return `This run's result arrived after this row was closed: the scan's current traits are this run's (source ${latestSourceId}).`;
}
