// The Video Jobs page's list: plate and cylinder scan jobs in one newest-first
// list, kept current by Realtime changes from both job tables.

import type { Change } from "@/lib/cyl-pipeline/realtime-reducer";
import type { Database } from "@/lib/database.types";
import { waveKey } from "@/app/app/plate-phenotypes/[speciesId]/[experimentId]/plateGrouping";

export const PLATE_JOBS_TABLE = "gravi_plate_video_jobs";
export const SCAN_JOBS_TABLE = "cyl_video_jobs";
export const JOB_PAGE_SIZE = 50;

// The queue's visibility timeout (VISIBILITY_TIMEOUT_SECONDS in video_queue.py):
// a rendering job reports at least this often, so one quieter may be stuck.
export const RENDERING_QUIET_MS = 5 * 60_000;
// A free worker claims a queued job within seconds; one still unclaimed after
// this, with nothing of its kind rendering, is waiting on a stopped worker.
export const UNCLAIMED_LONG_MS = 2 * 60_000;

type PlateRow = Database["public"]["Tables"]["gravi_plate_video_jobs"]["Row"];
type ScanRow = Database["public"]["Tables"]["cyl_video_jobs"]["Row"];

// Only the columns signed-in roles may read: not error_message, msg_id or reads.
type Shared = "id" | "experiment_id" | "status" | "stage" | "frames_done" | "frames_total"
  | "error_code" | "requested_by" | "created_at" | "started_at" | "updated_at" | "completed_at";

export type PlateJob = Pick<PlateRow, Shared | "plate_id" | "wave_number"> & { kind: "plate" };
export type ScanJob = Pick<ScanRow, Shared | "scan_id"> & { kind: "scan" };
export type ListedJob = PlateJob | ScanJob;
export type JobKind = ListedJob["kind"];

const SHARED_COLUMNS =
  "id, experiment_id, status, stage, frames_done, frames_total, error_code, requested_by, created_at, started_at, updated_at, completed_at";
export const PLATE_JOB_COLUMNS = `${SHARED_COLUMNS}, plate_id, wave_number`;
export const SCAN_JOB_COLUMNS = `${SHARED_COLUMNS}, scan_id`;

export const ACTIVE_STATUSES = ["queued", "rendering"] as const;
const STATUSES: readonly string[] = ["queued", "rendering", "rendered", "kept", "failed"];

/** A queued or rendering job as the queue sees it, whatever the page's filters. */
export interface ActiveJob {
  kind: JobKind;
  id: string;
  status: string;
  created_at: string;
  updated_at: string;
}
export const ACTIVE_JOB_COLUMNS = "id, status, created_at, updated_at";

export interface JobList {
  /** Newest first. */
  rows: ListedJob[];
  /** created_at of the oldest row a snapshot or "load older" read; null when none was. */
  cursor: string | null;
  hasOlder: boolean;
}

export function jobKey(job: Pick<ListedJob, "kind" | "id">): string {
  return `${job.kind}:${job.id}`;
}

export function compareJobsDesc(a: ListedJob, b: ListedJob): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1;
  return jobKey(a) < jobKey(b) ? 1 : jobKey(a) > jobKey(b) ? -1 : 0;
}

export function isActive(job: Pick<ListedJob, "status">): boolean {
  return (ACTIVE_STATUSES as readonly string[]).includes(job.status);
}

/** Two tables' pages merged into one, trimmed to a page so the cursor holds. */
export function mergePages(plates: PlateJob[], scans: ScanJob[], pageSize = JOB_PAGE_SIZE): {
  rows: ListedJob[];
  hasOlder: boolean;
} {
  const merged = [...plates, ...scans].sort(compareJobsDesc);
  return {
    rows: merged.slice(0, pageSize),
    hasOlder: merged.length > pageSize || plates.length >= pageSize || scans.length >= pageSize,
  };
}

export function listFromSnapshot(rows: ListedJob[], hasOlder: boolean): JobList {
  const sorted = [...rows].sort(compareJobsDesc);
  return { rows: sorted, cursor: sorted.at(-1)?.created_at ?? null, hasOlder };
}

/** Append a "load older" page, skipping rows already held. */
export function appendOlder(list: JobList, rows: ListedJob[], hasOlder: boolean): JobList {
  const held = new Set(list.rows.map(jobKey));
  const merged = [...list.rows, ...rows.filter((r) => !held.has(jobKey(r)))].sort(compareJobsDesc);
  return { rows: merged, cursor: rows.length ? merged.at(-1)!.created_at : list.cursor, hasOlder };
}

function kindOf(table: string): JobKind | null {
  if (table === PLATE_JOBS_TABLE) return "plate";
  if (table === SCAN_JOBS_TABLE) return "scan";
  return null;
}

const str = (v: unknown) => (typeof v === "string" ? v : null);
const num = (v: unknown) => (typeof v === "number" ? v : null);

/** A Realtime row as a job, keeping only the columns the page reads; null if it is not one. */
function jobFromChange(kind: JobKind, row: Record<string, unknown>): ListedJob | null {
  const id = str(row.id);
  const status = str(row.status);
  const created_at = str(row.created_at);
  const updated_at = str(row.updated_at);
  const experiment_id = num(row.experiment_id);
  if (!id || !status || !STATUSES.includes(status) || !created_at || !updated_at) return null;
  if (experiment_id === null) return null;
  const shared = {
    id,
    experiment_id,
    status,
    stage: str(row.stage),
    frames_done: num(row.frames_done),
    frames_total: num(row.frames_total),
    error_code: str(row.error_code),
    requested_by: str(row.requested_by),
    created_at,
    started_at: str(row.started_at),
    updated_at,
    completed_at: str(row.completed_at),
  };
  if (kind === "plate") {
    const plate_id = str(row.plate_id);
    return plate_id === null ? null : { ...shared, kind, plate_id, wave_number: num(row.wave_number) };
  }
  const scan_id = num(row.scan_id);
  return scan_id === null ? null : { ...shared, kind, scan_id };
}

/** Which table a change came from, as a job kind. */
export function changeKind(change: Change<unknown>): JobKind | null {
  return kindOf(change.table);
}

/**
 * Apply a job change. A held job is updated in place. An unknown job is added
 * only if `accept` takes it and it sorts within the loaded range.
 */
export function applyJobChange(
  list: JobList,
  change: Change<unknown>,
  accept: (job: ListedJob) => boolean = () => true
): JobList {
  const kind = kindOf(change.table);
  if (kind === null || (change.eventType !== "INSERT" && change.eventType !== "UPDATE")) return list;
  const incoming = change.new as Record<string, unknown>;
  const index = list.rows.findIndex((r) => r.kind === kind && r.id === incoming.id);
  if (index >= 0) {
    const fresh = jobFromChange(kind, { ...list.rows[index], ...incoming });
    if (!fresh) return list;
    const rows = [...list.rows];
    rows[index] = fresh;
    return { ...list, rows };
  }
  const job = jobFromChange(kind, incoming);
  if (!job || !accept(job)) return list;
  if (list.cursor && job.created_at < list.cursor) return list;
  return { ...list, rows: [...list.rows, job].sort(compareJobsDesc) };
}

/** The active set after a change: a queued or rendering job is held, any other dropped. */
export function applyActiveChange(active: ActiveJob[], change: Change<unknown>): ActiveJob[] {
  const kind = kindOf(change.table);
  if (kind === null || (change.eventType !== "INSERT" && change.eventType !== "UPDATE")) return active;
  const incoming = change.new as Record<string, unknown>;
  const id = str(incoming.id);
  if (!id) return active;
  const held = active.find((a) => a.kind === kind && a.id === id);
  const others = active.filter((a) => a !== held);
  const status = str(incoming.status) ?? held?.status ?? null;
  if (status === null || !(ACTIVE_STATUSES as readonly string[]).includes(status)) return others;
  const created_at = str(incoming.created_at) ?? held?.created_at;
  const updated_at = str(incoming.updated_at) ?? held?.updated_at ?? created_at;
  if (!created_at || !updated_at) return others;
  return [...others, { kind, id, status, created_at, updated_at }];
}

const earlier = (a: Pick<ActiveJob, "created_at" | "id">, b: Pick<ActiveJob, "created_at" | "id">) =>
  a.created_at < b.created_at || (a.created_at === b.created_at && a.id < b.id);

/** Queued jobs of the same kind asked for before this one: its place in its worker's line. */
export function queuedAhead(active: ActiveJob[], job: ListedJob): number | null {
  if (job.status !== "queued") return null;
  return active.filter((a) => a.kind === job.kind && a.status === "queued" && earlier(a, job)).length;
}

/** Why an active job looks slow, or null. */
export function overdue(job: ListedJob, now: number, active: ActiveJob[]): "quiet" | "waiting" | null {
  if (job.status === "rendering") {
    return now - Date.parse(job.updated_at) > RENDERING_QUIET_MS ? "quiet" : null;
  }
  if (job.status !== "queued" || now - Date.parse(job.created_at) <= UNCLAIMED_LONG_MS) return null;
  // Behind a render that is moving, a wait is just the line.
  const moving = active.some(
    (a) => a.kind === job.kind && a.status === "rendering" && now - Date.parse(a.updated_at) <= RENDERING_QUIET_MS
  );
  return moving ? null : "waiting";
}

// The plate page's own wave segment, so a link built here always lands on it.
export { waveKey };

// What the links back need beyond the job row.
export interface PlatePlace {
  experiment_id: number;
  name: string | null;
  species_id: number | null;
}
export interface ScanPlace {
  scan_id: number;
  experiment_name: string | null;
  species_id: number | null;
  wave_id: number | null;
  accession_id: number | null;
  plant_age_days: number | null;
}

/** The page the job's video plays on, or null while the lookup is missing a part. */
export function jobHref(
  job: ListedJob,
  plates: Map<number, PlatePlace>,
  scans: Map<number, ScanPlace>
): string | null {
  if (job.kind === "plate") {
    const place = plates.get(job.experiment_id);
    if (place?.species_id == null) return null;
    return `/app/plate-phenotypes/${place.species_id}/${job.experiment_id}/wave/${waveKey(job.wave_number)}/${encodeURIComponent(job.plate_id)}`;
  }
  const place = scans.get(job.scan_id);
  if (place?.species_id == null || place.wave_id == null || place.accession_id == null) return null;
  return `/app/phenotypes/${place.species_id}/${job.experiment_id}/${place.wave_id}/${place.accession_id}/${job.scan_id}`;
}
