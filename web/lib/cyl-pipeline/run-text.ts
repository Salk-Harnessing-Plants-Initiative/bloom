/** Short texts for pipeline-run views: a run's target and requester, and the shared pluraliser. */

import type { RunRow } from "./realtime-reducer";

/** "1 scan", "0 scans", "40 scans". */
export const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** "scan 577 · 1 scan", "wave 3 · 40 scans", or "3 selected scans" ("1 selected scan") for a scan_ids run. */
export function targetText(run: Pick<RunRow, "target_level" | "target_id" | "scan_count">): string {
  if (run.target_level === "scan_ids") return plural(run.scan_count, "selected scan");
  const target = run.target_id == null ? run.target_level : `${run.target_level} ${run.target_id}`;
  return `${target} · ${plural(run.scan_count, "scan")}`;
}

/**
 * "you", or "another member · <first 8 characters of requested_by>".
 * Display names are out of scope: `phenotypers` is invisible to bloom_user.
 */
export function requesterText(requestedBy: string | null, currentUserId: string | null): string {
  if (!requestedBy) return "unknown requester";
  if (currentUserId && requestedBy === currentUserId) return "you";
  return `another member · ${requestedBy.slice(0, 8)}`;
}
