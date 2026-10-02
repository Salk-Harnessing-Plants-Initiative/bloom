/**
 * Hints for a failed run-scan row in the drill-down (design D7).
 */

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
 * `fail_cyl_pipeline_run_scans_without_result` at the end of each batch),
 * for example after a stage-in failure (a poison scan). failedScanCause still
 * gives that row its likely cause. failure-hints.test.ts reads ingest.py to
 * keep the two equal.
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
