// Reads for the Video Jobs page, with the caller's session. Signed-in roles may
// read both job tables, column by column.

import type { ReadClient } from "@/lib/cyl-pipeline/queries";
import {
  ACTIVE_STATUSES,
  JOB_PAGE_SIZE,
  PLATE_JOBS_TABLE,
  PLATE_JOB_COLUMNS,
  SCAN_JOBS_TABLE,
  SCAN_JOB_COLUMNS,
  mergePages,
  type JobKind,
  type ListedJob,
  type PlateJob,
  type PlatePlace,
  type ScanJob,
  type ScanPlace,
} from "./list";

export interface JobFilters {
  /** Only this user's jobs; null for everyone's. */
  requestedBy?: string | null;
  /** Only one kind; null for both. */
  kind?: JobKind | null;
  activeOnly?: boolean;
  /** Only jobs created at or before this; null for the newest page. */
  before?: string | null;
}

async function read<T>(table: string, query: PromiseLike<{ data: T | null; error: { message: string } | null }>) {
  const { data, error } = await query;
  if (error) throw new Error(`${table}: ${error.message}`);
  return data;
}

function page(client: ReadClient, table: string, columns: string, f: JobFilters) {
  let query = client.from(table).select(columns);
  if (f.requestedBy) query = query.eq("requested_by", f.requestedBy);
  if (f.activeOnly) query = query.in("status", [...ACTIVE_STATUSES]);
  // At or before: a row sharing the cursor's instant is re-read and dropped as held.
  if (f.before) query = query.lte("created_at", f.before);
  return query.order("created_at", { ascending: false }).limit(JOB_PAGE_SIZE);
}

/** One page of jobs from both tables, newest first. */
export async function fetchJobs(
  client: ReadClient,
  filters: JobFilters = {}
): Promise<{ rows: ListedJob[]; hasOlder: boolean }> {
  const want = (kind: JobKind) => !filters.kind || filters.kind === kind;
  const [plates, scans] = await Promise.all([
    want("plate")
      ? read<Omit<PlateJob, "kind">[]>(PLATE_JOBS_TABLE, page(client, PLATE_JOBS_TABLE, PLATE_JOB_COLUMNS, filters))
      : [],
    want("scan")
      ? read<Omit<ScanJob, "kind">[]>(SCAN_JOBS_TABLE, page(client, SCAN_JOBS_TABLE, SCAN_JOB_COLUMNS, filters))
      : [],
  ]);
  return mergePages(
    (plates ?? []).map((j) => ({ ...j, kind: "plate" as const })),
    (scans ?? []).map((j) => ({ ...j, kind: "scan" as const }))
  );
}

/** Where each job's video lives: names and the ids its page's address needs. */
export async function fetchJobPlaces(
  client: ReadClient,
  jobs: ListedJob[]
): Promise<{ plates: PlatePlace[]; scans: ScanPlace[] }> {
  const experiments = [...new Set(jobs.filter((j) => j.kind === "plate").map((j) => j.experiment_id))];
  const scanIds = [...new Set(jobs.flatMap((j) => (j.kind === "scan" ? [j.scan_id] : [])))];
  const [plates, scans] = await Promise.all([
    experiments.length
      ? read<{ id: number; name: string | null; species_id: number | null }[]>(
          "gravi_experiments",
          client.from("gravi_experiments").select("id, name, species_id").in("id", experiments)
        )
      : [],
    scanIds.length
      ? read<ScanPlace[]>(
          "cyl_scans_extended",
          client
            .from("cyl_scans_extended")
            .select("scan_id, experiment_name, species_id, wave_id, accession_id, plant_age_days")
            .in("scan_id", scanIds)
        )
      : [],
  ]);
  return {
    plates: (plates ?? []).map((p) => ({ experiment_id: p.id, name: p.name, species_id: p.species_id })),
    scans: scans ?? [],
  };
}
