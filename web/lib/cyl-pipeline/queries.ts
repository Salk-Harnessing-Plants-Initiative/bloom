/**
 * Reads for the live pipeline-run views. Every read goes through PostgREST as
 * the caller, so RLS applies (`bloom_user` has `SELECT USING (true)` on both
 * run tables). Id lists are chunked to at most 200 ids and run-scan rows are
 * paged by 1000, because an unpaged `in.(…)` overflows the gateway's URL limit
 * (bloom#674) and a large read can hit the 8 s statement timeout.
 *
 * Runs reach traits only through their requested scans (design D6); nothing
 * here reads trait sources by run.
 */

import { compareRunsDesc, PANEL_SIZE, RUN_PAGE_SIZE, type Cursor, type RunRow, type RunScanRow } from "./realtime-reducer";
import { runDisplay } from "./run-display";
import { SCAN_META_COLUMNS, type ScanMeta } from "./scan-meta";
import type { TriggerTarget } from "./trigger-target";

export class QueryError extends Error {
  constructor(
    readonly relation: string,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "QueryError";
  }
}

// One definition each: the list decides "has older" from the same page size.
export const RUNS_PAGE = RUN_PAGE_SIZE;
export const RUN_SCANS_PAGE = 1000;
export const ID_CHUNK = 200;
export const PANEL_RUNS = PANEL_SIZE;

/** Every run column the views show; `reused_count` is left out (design D3). */
export const RUN_COLUMNS =
  "id, created_at, requested_by, target_level, target_id, params, status, scan_count, done_count, failed_count, error_message, submitted_at, completed_at";

const RUN_SCAN_COLUMNS =
  "id, run_id, scan_id, status, attempts, error_message, argo_workflow_name, batch_index, source_id, created_at, updated_at";

export interface RunExperiment {
  run_id: number;
  experiment_id: number;
  created_at: string;
  name: string | null;
  species_id: number | null;
}

// Structural, so the browser and server Supabase clients (and the test double) all fit.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ReadClient = { from: (relation: string) => any };

interface Result<T> {
  data: T | null;
  error: { message: string; code?: string } | null;
}

async function read<T>(relation: string, query: PromiseLike<Result<T>>): Promise<T> {
  const { data, error } = await query;
  if (error) throw new QueryError(relation, error.message, error.code ?? null);
  return data as T;
}

function chunks<T>(items: T[], size = ID_CHUNK): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** Read each chunk in turn and concatenate the rows. */
async function readChunked<R>(ids: number[], readChunk: (chunk: number[]) => Promise<R[]>): Promise<R[]> {
  const rows: R[] = [];
  for (const chunk of chunks(ids)) rows.push(...(await readChunk(chunk)));
  return rows;
}

/**
 * The newest runs, or those older than `cursor` by keyset. The cursor's raw
 * `created_at` goes in double quotes: it contains `:` and `+`, which PostgREST
 * would otherwise read as syntax.
 */
export async function fetchRuns(
  client: ReadClient,
  { cursor = null, requestedBy = null }: { cursor?: Cursor | null; requestedBy?: string | null } = {},
): Promise<RunRow[]> {
  let query = client.from("cyl_pipeline_runs").select(RUN_COLUMNS);
  if (requestedBy) query = query.eq("requested_by", requestedBy);
  if (cursor) {
    const at = `"${cursor.created_at}"`;
    query = query.or(`created_at.lt.${at},and(created_at.eq.${at},id.lt.${cursor.id})`);
  }
  query = query.order("created_at", { ascending: false }).order("id", { ascending: false }).limit(RUNS_PAGE);
  return (await read<RunRow[]>("cyl_pipeline_runs", query)) ?? [];
}

/** One run, or null when it doesn't exist or isn't visible to the caller. */
export async function fetchRun(client: ReadClient, id: number): Promise<RunRow | null> {
  return read<RunRow | null>("cyl_pipeline_runs", client.from("cyl_pipeline_runs").select(RUN_COLUMNS).eq("id", id).maybeSingle());
}

export async function fetchRunsByIds(client: ReadClient, ids: number[]): Promise<RunRow[]> {
  return readChunked(ids, async (chunk) =>
    (await read<RunRow[]>("cyl_pipeline_runs", client.from("cyl_pipeline_runs").select(RUN_COLUMNS).in("id", chunk))) ?? [],
  );
}

/** Every run-scan row of a run, in pages of 1000 ordered by `scan_id`, until a page is empty. */
export async function fetchRunScans(client: ReadClient, runId: number): Promise<RunScanRow[]> {
  const rows: RunScanRow[] = [];
  for (let from = 0; ; from += RUN_SCANS_PAGE) {
    const page =
      (await read<RunScanRow[]>(
        "cyl_pipeline_run_scans",
        client
          .from("cyl_pipeline_run_scans")
          .select(RUN_SCAN_COLUMNS)
          .eq("run_id", runId)
          .order("scan_id", { ascending: true })
          .range(from, from + RUN_SCANS_PAGE - 1),
      )) ?? [];
    if (page.length === 0) return rows;
    rows.push(...page);
  }
}

/** Each scan's plant, wave, age, species and accession, keyed by scan id. */
export async function fetchScanMeta(client: ReadClient, ids: number[]): Promise<Map<number, ScanMeta>> {
  const rows = await readChunked(ids, async (chunk) =>
    (await read<ScanMeta[]>(
      "cyl_scans_extended",
      client.from("cyl_scans_extended").select(SCAN_META_COLUMNS).in("scan_id", chunk),
    )) ?? [],
  );
  return new Map(rows.map((m) => [m.scan_id, m]));
}

/**
 * Each scan's latest trait source, keyed by scan id. A scan that never had
 * traits has no entry; one whose traits have no source, or whose traits were
 * all deleted, maps to null.
 */
export async function fetchLatestSources(client: ReadClient, ids: number[]): Promise<Map<number, number | null>> {
  const rows = await readChunked(ids, async (chunk) =>
    (await read<{ scan_id: number; max_source_id: number | null }[]>(
      "cyl_scan_latest_source",
      client.from("cyl_scan_latest_source").select("scan_id, max_source_id").in("scan_id", chunk),
    )) ?? [],
  );
  return new Map(rows.map((r) => [r.scan_id, r.max_source_id]));
}

type ViewRow = {
  run_id: number;
  experiment_id: number;
  created_at: string;
  cyl_experiments: { name: string | null; species_id: number | null } | null;
};

/**
 * The experiments each run touches, from `cyl_pipeline_run_experiments`. The
 * view drops soft-deleted experiments for `bloom_user`, so such a run has no
 * row here.
 */
export async function fetchRunExperiments(client: ReadClient, runIds: number[]): Promise<RunExperiment[]> {
  const rows = await readChunked(runIds, async (chunk) =>
    (await read<ViewRow[]>(
      "cyl_pipeline_run_experiments",
      client
        .from("cyl_pipeline_run_experiments")
        .select("run_id, experiment_id, created_at, cyl_experiments(name, species_id)")
        .in("run_id", chunk)
        .order("created_at", { ascending: false }),
    )) ?? [],
  );
  return rows.map(({ cyl_experiments, ...row }) => ({
    ...row,
    name: cyl_experiments?.name ?? null,
    species_id: cyl_experiments?.species_id ?? null,
  }));
}

/** The 10 most recent runs that include at least one of an experiment's scans. */
export async function fetchExperimentRunIds(client: ReadClient, experimentId: number): Promise<number[]> {
  const rows =
    (await read<{ run_id: number }[]>(
      "cyl_pipeline_run_experiments",
      client
        .from("cyl_pipeline_run_experiments")
        .select("run_id, created_at")
        .eq("experiment_id", experimentId)
        .order("created_at", { ascending: false })
        .order("run_id", { ascending: false })
        .limit(PANEL_RUNS),
    )) ?? [];
  return rows.map((r) => r.run_id);
}

/** Which of `runIds` include at least one of the experiment's scans. */
export async function fetchExperimentMembers(client: ReadClient, experimentId: number, runIds: number[]): Promise<Set<number>> {
  const rows = await readChunked(runIds, async (chunk) =>
    (await read<{ run_id: number }[]>(
      "cyl_pipeline_run_experiments",
      client.from("cyl_pipeline_run_experiments").select("run_id").eq("experiment_id", experimentId).in("run_id", chunk),
    )) ?? [],
  );
  return new Set(rows.map((r) => r.run_id));
}

export async function isRunInExperiment(client: ReadClient, runId: number, experimentId: number): Promise<boolean> {
  return (await fetchExperimentMembers(client, experimentId, [runId])).has(runId);
}

const TARGET_FILTER = { scan: "scan_id", wave: "wave_id", experiment: "experiment_id" } as const;

/**
 * The scans a run action would send, from `cyl_scans_extended` with the
 * trigger's own filters (`_enumerate` in services/workflows/pipeline.py), so
 * the dialog's N matches the trigger's count (design D4). A single target is
 * read in pages of 1000 ordered by `scan_id` until a page is empty; a
 * `scan_ids` selection is de-duplicated, as the trigger's own existence check
 * is, and sent in chunks of at most 200 ids.
 */
export async function fetchTargetScans(client: ReadClient, target: TriggerTarget): Promise<ScanMeta[]> {
  if (target.target_level === "scan_ids") {
    return readChunked([...new Set(target.scan_ids)], async (chunk) =>
      (await read<ScanMeta[]>(
        "cyl_scans_extended",
        client.from("cyl_scans_extended").select(SCAN_META_COLUMNS).in("scan_id", chunk).order("scan_id", { ascending: true }),
      )) ?? [],
    );
  }
  const column = TARGET_FILTER[target.target_level];
  const rows: ScanMeta[] = [];
  for (let from = 0; ; from += TARGET_SCANS_PAGE) {
    const page =
      (await read<ScanMeta[]>(
        "cyl_scans_extended",
        client
          .from("cyl_scans_extended")
          .select(SCAN_META_COLUMNS)
          .eq(column, target.target_id)
          .order("scan_id", { ascending: true })
          .range(from, from + TARGET_SCANS_PAGE - 1),
      )) ?? [];
    if (page.length === 0) return rows;
    rows.push(...page);
  }
}

export const TARGET_SCANS_PAGE = 1000;
export const CONCURRENT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
export const CONCURRENT_RUNS_SHOWN = 10;

export interface ConcurrentRuns {
  /** At most CONCURRENT_RUNS_SHOWN, newest first. */
  runs: RunRow[];
  /** How many more matched beyond those. */
  more: number;
}

/**
 * Runs that may still be working on the same experiments (spec: the confirm
 * dialog's concurrent runs; design D4): touching one of `experimentIds` per
 * `cyl_pipeline_run_experiments`, created within 7 days (the view carries each
 * run's `created_at`), `status` not `complete` or `failed`, and counts
 * incomplete (a client-side filter, since PostgREST can't compare two
 * columns). Membership is read first, so unfinished runs on other experiments
 * can't crowd these out, and `more` is the true count. Runs frozen by
 * #706/#710 never settle, which is why each shows its counts-first state and
 * age rather than "in progress".
 */
export async function fetchConcurrentRuns(client: ReadClient, experimentIds: number[], now = Date.now()): Promise<ConcurrentRuns> {
  if (experimentIds.length === 0) return { runs: [], more: 0 };
  const since = new Date(now - CONCURRENT_WINDOW_MS).toISOString();
  const touching = await readChunked(experimentIds, async (chunk) =>
    (await read<{ run_id: number }[]>(
      "cyl_pipeline_run_experiments",
      client.from("cyl_pipeline_run_experiments").select("run_id").in("experiment_id", chunk).gte("created_at", since),
    )) ?? [],
  );
  const runIds = [...new Set(touching.map((r) => r.run_id))];
  const unfinished = await readChunked(runIds, async (chunk) =>
    (await read<RunRow[]>(
      "cyl_pipeline_runs",
      client.from("cyl_pipeline_runs").select(RUN_COLUMNS).in("id", chunk).not("status", "in", "(complete,failed)"),
    )) ?? [],
  );
  const incomplete = unfinished.filter((r) => runDisplay(r).counts.U > 0).sort(compareRunsDesc);
  return { runs: incomplete.slice(0, CONCURRENT_RUNS_SHOWN), more: Math.max(0, incomplete.length - CONCURRENT_RUNS_SHOWN) };
}

/**
 * Which of `ids` have at least one image. Stage-in fails a scan with none
 * ("No frames found" in bloomctl's download_for_predict), and "Run this
 * accession" sends scans the grid doesn't show. Each scan embeds at most one
 * image id, so a chunk of 200 reads at most 200 image rows, not a rotation's
 * ~72 frames per scan.
 */
export async function fetchScansWithImages(client: ReadClient, ids: number[]): Promise<Set<number>> {
  const rows = await readChunked(ids, async (chunk) =>
    (await read<{ id: number; cyl_images: { id: number }[] | null }[]>(
      "cyl_scans",
      client.from("cyl_scans").select("id, cyl_images(id)").in("id", chunk).limit(1, { referencedTable: "cyl_images" }),
    )) ?? [],
  );
  return new Set(rows.filter((r) => (r.cyl_images?.length ?? 0) > 0).map((r) => r.id));
}
