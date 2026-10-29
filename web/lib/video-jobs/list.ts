// The Video Jobs page's list: plate and cylinder scan jobs in one newest-first
// list, kept current by Realtime changes from both job tables.

import type { Change } from "@/lib/cyl-pipeline/realtime-reducer";
import type { Database } from "@/lib/database.types";

export const PLATE_JOBS_TABLE = "gravi_plate_video_jobs";
export const SCAN_JOBS_TABLE = "cyl_video_jobs";
export const JOB_PAGE_SIZE = 50;

// The worker renews a rendering job at least this often (the queue's
// visibility timeout); a rendering job quieter than this may be stuck.
export const RENDERING_QUIET_MS = 5 * 60_000;
// Longer than a few plates ahead would take; a job still queued after this is
// waiting longer than usual, most likely on a stopped worker.
export const QUEUED_LONG_MS = 15 * 60_000;

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

/** A Realtime change as a job, keeping only the columns the page reads. */
function jobFromChange(kind: JobKind, row: Record<string, unknown>): ListedJob | null {
  if (typeof row.id !== "string" || typeof row.created_at !== "string") return null;
  const shared = {
    id: row.id,
    experiment_id: row.experiment_id as number,
    status: row.status as string,
    stage: (row.stage as string | null) ?? null,
    frames_done: (row.frames_done as number | null) ?? null,
    frames_total: (row.frames_total as number | null) ?? null,
    error_code: (row.error_code as string | null) ?? null,
    requested_by: (row.requested_by as string | null) ?? null,
    created_at: row.created_at,
    started_at: (row.started_at as string | null) ?? null,
    updated_at: row.updated_at as string,
    completed_at: (row.completed_at as string | null) ?? null,
  };
  return kind === "plate"
    ? { ...shared, kind, plate_id: row.plate_id as string, wave_number: (row.wave_number as number | null) ?? null }
    : { ...shared, kind, scan_id: row.scan_id as number };
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

/** Queued jobs of the same kind created before this one: its place in its worker's line. */
export function queuedAhead(rows: ListedJob[], job: ListedJob): number | null {
  if (job.status !== "queued") return null;
  return rows.filter(
    (r) => r.kind === job.kind && r.status === "queued" && compareJobsDesc(r, job) > 0
  ).length;
}

/** Why an active job looks slow, or null. */
export function overdue(job: ListedJob, now: number): "quiet" | "waiting" | null {
  if (job.status === "rendering" && now - Date.parse(job.updated_at) > RENDERING_QUIET_MS) return "quiet";
  if (job.status === "queued" && now - Date.parse(job.created_at) > QUEUED_LONG_MS) return "waiting";
  return null;
}

/** The plate page's wave segment: the number, or "none" for a plate with no wave. */
export function waveKey(waveNumber: number | null): string {
  return waveNumber === null ? "none" : String(waveNumber);
}

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
