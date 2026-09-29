/**
 * Pure state for the live pipeline-run views (design D2).
 *
 * - **Buffer.** Events that arrive while a snapshot fetch is in flight are
 *   held and replayed on top of the snapshot. Realtime delivers a channel's
 *   changes in commit order, so snapshot plus replay converges.
 * - **Merge, not replace.** An unchanged TOASTed column (`error_message`) is
 *   absent from UPDATE payloads (task 0.3), so absent keys keep held values.
 * - **Counts only grow** within a held run. A snapshot replaces the held row,
 *   so a resync still corrects an admin edit.
 * - **No `updated_at` guard.** `now()` is the transaction start, so an
 *   older-starting transaction that commits later would be dropped.
 * - **List window.** The cursor is the raw `(created_at, id)` of the oldest
 *   row loaded by a snapshot or "load older", and moves only then. `partial`
 *   and `running` runs get an UPDATE every sweep; inserting an older one into
 *   the window would make "load older" skip the runs between.
 */

import type { Database } from "@/lib/database.types";
import { compareTimestamps } from "./timestamps";

export type RunRow = Database["public"]["Tables"]["cyl_pipeline_runs"]["Row"];
export type RunScanRow = Database["public"]["Tables"]["cyl_pipeline_run_scans"]["Row"];

export const RUNS_TABLE = "cyl_pipeline_runs";
export const RUN_SCANS_TABLE = "cyl_pipeline_run_scans";

/** The parts of a Realtime `postgres_changes` payload the views read. */
export interface Change<T> {
  table: string;
  eventType: string;
  new: Partial<T>;
}

export interface Synced<V, E> {
  view: V;
  fetching: boolean;
  buffer: E[];
}

export interface Cursor {
  created_at: string;
  id: number;
}

export interface RunList {
  /** Newest first. */
  rows: RunRow[];
  /** The oldest row loaded by a snapshot or "load older"; null when none was. */
  cursor: Cursor | null;
  hasOlder: boolean;
}

export interface RunDetail {
  run: RunRow;
  /** Keyed by the run-scan row's `id`. */
  scans: Map<number, RunScanRow>;
}

export const RUN_PAGE_SIZE = 50;
export const PANEL_SIZE = 10;

const isChange = (e: Change<unknown>) => e.eventType === "INSERT" || e.eventType === "UPDATE";

export function mergeRun(held: RunRow | undefined, incoming: Partial<RunRow>): RunRow {
  if (!held) return { ...incoming } as RunRow;
  const merged = { ...held, ...incoming };
  merged.done_count = Math.max(held.done_count ?? 0, incoming.done_count ?? 0);
  merged.failed_count = Math.max(held.failed_count ?? 0, incoming.failed_count ?? 0);
  return merged;
}

export function mergeScanRow(held: RunScanRow | undefined, incoming: Partial<RunScanRow>): RunScanRow {
  return { ...held, ...incoming } as RunScanRow;
}

/** Newest first: `created_at` descending, then `id` descending. */
export function compareRunsDesc(a: Pick<Cursor, "created_at" | "id">, b: Pick<Cursor, "created_at" | "id">): number {
  return compareTimestamps(b.created_at, a.created_at) || b.id - a.id;
}

export function synced<V, E>(view: V): Synced<V, E> {
  return { view, fetching: false, buffer: [] };
}

/** Start buffering. A fetch that overlaps one in flight keeps the buffer. */
export function beginFetch<V, E>(s: Synced<V, E>): Synced<V, E> {
  return { ...s, fetching: true, buffer: s.fetching ? s.buffer : [] };
}

export function receive<V, E>(s: Synced<V, E>, event: E, apply: (v: V, e: E) => V): Synced<V, E> {
  return s.fetching ? { ...s, buffer: [...s.buffer, event] } : { ...s, view: apply(s.view, event) };
}

export function endFetch<V, E>(s: Synced<V, E>, snapshot: V, apply: (v: V, e: E) => V): Synced<V, E> {
  return { view: s.buffer.reduce(apply, snapshot), fetching: false, buffer: [] };
}

export function failFetch<V, E>(s: Synced<V, E>, apply: (v: V, e: E) => V): Synced<V, E> {
  return { view: s.buffer.reduce(apply, s.view), fetching: false, buffer: [] };
}

const cursorOf = (row: RunRow): Cursor => ({ created_at: row.created_at, id: row.id });

export function listFromSnapshot(rows: RunRow[], pageSize = RUN_PAGE_SIZE): RunList {
  const sorted = [...rows].sort(compareRunsDesc);
  return {
    rows: sorted,
    cursor: sorted.length ? cursorOf(sorted[sorted.length - 1]) : null,
    hasOlder: rows.length >= pageSize,
  };
}

/**
 * Apply a run event to the list. A held run is merged in place. An unknown run
 * is inserted only when no row was loaded, or when it sorts at or after the
 * cursor; `accept` (the "Only mine" filter) can reject it first.
 */
export function applyListChange(
  list: RunList,
  change: Change<RunRow>,
  accept?: (row: Partial<RunRow>) => boolean,
): RunList {
  if (change.table !== RUNS_TABLE || !isChange(change) || typeof change.new.id !== "number") return list;
  const index = list.rows.findIndex((r) => r.id === change.new.id);
  if (index >= 0) {
    const rows = [...list.rows];
    rows[index] = mergeRun(rows[index], change.new);
    return { ...list, rows };
  }
  if (accept && !accept(change.new)) return list;
  const row = mergeRun(undefined, change.new);
  if (list.cursor && compareRunsDesc(row, list.cursor) > 0) return list;
  return { ...list, rows: [...list.rows, row].sort(compareRunsDesc) };
}

/** Append a "load older" page, skipping rows already held. */
export function appendOlder(list: RunList, rows: RunRow[], pageSize = RUN_PAGE_SIZE): RunList {
  const held = new Set(list.rows.map((r) => r.id));
  const fresh = rows.filter((r) => !held.has(r.id));
  const oldest = [...rows].sort(compareRunsDesc).at(-1);
  return {
    rows: [...list.rows, ...fresh].sort(compareRunsDesc),
    cursor: oldest ? cursorOf(oldest) : list.cursor,
    hasOlder: rows.length >= pageSize,
  };
}

export function detailFromSnapshot(run: RunRow, scans: RunScanRow[]): RunDetail {
  return { run, scans: new Map(scans.map((s) => [s.id, s])) };
}

/** Apply an event to one run's drill-down; events for other runs are ignored. */
export function applyDetailChange(detail: RunDetail, change: Change<RunRow | RunScanRow>, runId: number): RunDetail {
  if (!isChange(change)) return detail;
  if (change.table === RUNS_TABLE) {
    const incoming = change.new as Partial<RunRow>;
    if (incoming.id !== runId) return detail;
    return { ...detail, run: mergeRun(detail.run, incoming) };
  }
  if (change.table === RUN_SCANS_TABLE) {
    const incoming = change.new as Partial<RunScanRow>;
    if (incoming.run_id !== runId || typeof incoming.id !== "number") return detail;
    const scans = new Map(detail.scans);
    scans.set(incoming.id, mergeScanRow(scans.get(incoming.id), incoming));
    return { ...detail, scans };
  }
  return detail;
}

/** Header tallies: `written` and `reused` are done, `failed` is failed. */
export function countsFromScanRows(rows: Iterable<Pick<RunScanRow, "status">>): { done: number; failed: number } {
  let done = 0;
  let failed = 0;
  for (const { status } of rows) {
    if (status === "written" || status === "reused") done += 1;
    else if (status === "failed") failed += 1;
  }
  return { done, failed };
}

/** Add (or merge) a run and keep the `limit` most recent. */
export function addPanelRun(runs: RunRow[], row: RunRow, limit = PANEL_SIZE): RunRow[] {
  const index = runs.findIndex((r) => r.id === row.id);
  const next = [...runs];
  if (index >= 0) next[index] = mergeRun(next[index], row);
  else next.push(row);
  return next.sort(compareRunsDesc).slice(0, limit);
}

export function applyPanelChange(runs: RunRow[], change: Change<RunRow>): { runs: RunRow[]; held: boolean } {
  if (change.table !== RUNS_TABLE || !isChange(change)) return { runs, held: false };
  const index = runs.findIndex((r) => r.id === change.new.id);
  if (index < 0) return { runs, held: false };
  const next = [...runs];
  next[index] = mergeRun(next[index], change.new);
  return { runs: next, held: true };
}
