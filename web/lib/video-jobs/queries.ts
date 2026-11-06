// Reads for the Video Jobs page, with the caller's session. Signed-in roles may
// read both job tables, column by column.

import type { ReadClient } from "@/lib/cyl-pipeline/queries";
import {
  ACTIVE_JOB_COLUMNS,
  ACTIVE_STATUSES,
  JOB_PAGE_SIZE,
  PLATE_JOBS_TABLE,
  PLATE_JOB_COLUMNS,
  SCAN_JOBS_TABLE,
  SCAN_JOB_COLUMNS,
  mergePages,
  type ActiveJob,
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

// Far more than one worker per kind could have waiting; a guard, not a page.
const ACTIVE_LIMIT = 1000;

/** Every queued or rendering job of both kinds, whoever asked: the queue's own order. */
export async function fetchActiveJobs(client: ReadClient): Promise<ActiveJob[]> {
  const read1 = (table: string, kind: JobKind) =>
    read<Omit<ActiveJob, "kind">[]>(
      table,
      client
        .from(table)
        .select(ACTIVE_JOB_COLUMNS)
        .in("status", [...ACTIVE_STATUSES])
        .order("created_at", { ascending: true })
        .limit(ACTIVE_LIMIT)
    ).then((rows) => (rows ?? []).map((r) => ({ ...r, kind })));
  const [plates, scans] = await Promise.all([
    read1(PLATE_JOBS_TABLE, "plate"),
    read1(SCAN_JOBS_TABLE, "scan"),
  ]);
  return [...plates, ...scans];
}

/** One job by id, whichever table holds it; null when neither does. */
export async function fetchJobById(client: ReadClient, id: string): Promise<ListedJob | null> {
  const one = async <T,>(table: string, columns: string) =>
    (await read<T[]>(table, client.from(table).select(columns).eq("id", id).limit(1)))?.[0] ?? null;
  const [plate, scan] = await Promise.all([
    one<Omit<PlateJob, "kind">>(PLATE_JOBS_TABLE, PLATE_JOB_COLUMNS),
    one<Omit<ScanJob, "kind">>(SCAN_JOBS_TABLE, SCAN_JOB_COLUMNS),
  ]);
  if (plate) return { ...plate, kind: "plate" };
  if (scan) return { ...scan, kind: "scan" };
  return null;
}

/**
 * Where each job's video lives: names and the ids its page's address needs.
 *
 * Experiment names and species come from the experiment tables, under the
 * caller's RLS; `cyl_scans_extended` runs as its owner and lists soft-deleted
 * experiments too, so only the scan's own ids are taken from it.
 */
export async function fetchJobPlaces(
  client: ReadClient,
  jobs: ListedJob[]
): Promise<{ plates: PlatePlace[]; scans: ScanPlace[] }> {
  const plateExperiments = [
    ...new Set(jobs.filter((j) => j.kind === "plate").map((j) => j.experiment_id)),
  ];
  const scanJobs = jobs.filter((j): j is ScanJob => j.kind === "scan");
  const scanIds = [...new Set(scanJobs.map((j) => j.scan_id))];
  const cylExperiments = [...new Set(scanJobs.map((j) => j.experiment_id))];
  type Experiment = { id: number; name: string | null; species_id: number | null };
  type ScanIds = { scan_id: number; wave_id: number | null; accession_id: number | null; plant_age_days: number | null };
  const [plates, cyl, scans] = await Promise.all([
    plateExperiments.length
      ? read<Experiment[]>(
          "gravi_experiments",
          client.from("gravi_experiments").select("id, name, species_id").in("id", plateExperiments)
        )
      : [],
    cylExperiments.length
      ? read<Experiment[]>(
          "cyl_experiments",
          client.from("cyl_experiments").select("id, name, species_id").in("id", cylExperiments)
        )
      : [],
    scanIds.length
      ? read<ScanIds[]>(
          "cyl_scans_extended",
          client
            .from("cyl_scans_extended")
            .select("scan_id, wave_id, accession_id, plant_age_days")
            .in("scan_id", scanIds)
        )
      : [],
  ]);
  const experiment = new Map((cyl ?? []).map((e) => [e.id, e]));
  const experimentOf = new Map(scanJobs.map((j) => [j.scan_id, j.experiment_id]));
  return {
    plates: (plates ?? []).map((p) => ({ experiment_id: p.id, name: p.name, species_id: p.species_id })),
    scans: (scans ?? []).map((sc) => {
      const e = experiment.get(experimentOf.get(sc.scan_id) ?? -1);
      // An experiment the caller cannot read gets no name and no link.
      return { ...sc, experiment_name: e?.name ?? null, species_id: e?.species_id ?? null };
    }),
  };
}
