/** Short texts for a run row: its target and its requester. */

import type { RunRow } from "./realtime-reducer";

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** "scan 577 · 1 scan", "wave 3 · 40 scans", or "3 selected scans" for a scan_ids run. */
export function targetText(run: Pick<RunRow, "target_level" | "target_id" | "scan_count">): string {
  if (run.target_level === "scan_ids") return `${run.scan_count} selected scan${run.scan_count === 1 ? "" : "s"}`;
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
