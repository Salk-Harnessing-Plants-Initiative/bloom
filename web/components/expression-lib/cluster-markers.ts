import { createClientSupabaseClient } from "@/lib/supabase/client";

/**
 * Shape of the `markers` JSON column on `public.scrna_cluster_stats`.
 * Written by the ingest pipeline; consumed read-only by the cluster
 * detail panel.
 */
export type ClusterMarker = {
  gene: string;
  log2fc: number;
  q: number;
  pct_1: number;
  pct_2: number;
  /** The gene's symbol, e.g. PELPK1, where it has one. */
  symbol?: string | null;
  /** The atlas the marker's cell-type label came from, e.g. "nuclei", where known. */
  source?: string | null;
};

export type ClusterMarkers = {
  top: ClusterMarker[];
  n_significant: number;
};

export type ClusterStatsRow = {
  dataset_id: number;
  cluster_id: string;
  cell_count: number;
  pct: number;
  markers: ClusterMarkers | null;
};

/**
 * Defensive parse — returns null for anything that doesn't match the
 * documented shape. Lets the panel render its empty state instead of
 * crashing on a half-populated or legacy row.
 */
export function parseMarkers(raw: unknown): ClusterMarkers | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const obj = raw as Record<string, unknown>;
  const top = obj.top;
  if (!Array.isArray(top)) return null;
  const parsedTop: ClusterMarker[] = [];
  for (const m of top) {
    if (!m || typeof m !== "object") continue;
    const r = m as Record<string, unknown>;
    if (typeof r.gene !== "string") continue;
    parsedTop.push({
      gene: r.gene,
      log2fc: typeof r.log2fc === "number" ? r.log2fc : 0,
      q: typeof r.q === "number" ? r.q : 1,
      pct_1: typeof r.pct_1 === "number" ? r.pct_1 : 0,
      pct_2: typeof r.pct_2 === "number" ? r.pct_2 : 0,
      symbol: typeof r.symbol === "string" && r.symbol ? r.symbol : null,
      source: typeof r.source === "string" && r.source ? r.source : null,
    });
  }
  const n_significant =
    typeof obj.n_significant === "number" ? obj.n_significant : 0;
  return { top: parsedTop, n_significant };
}

/**
 * Fetch the stats row for one cluster.
 * Returns null when the row is missing.
 */
export async function fetchClusterStats(
  datasetId: number,
  clusterId: string,
): Promise<ClusterStatsRow | null> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_cluster_stats")
    .select("dataset_id, cluster_id, cell_count, pct, markers")
    .eq("dataset_id", datasetId)
    .eq("cluster_id", clusterId)
    .maybeSingle();
  if (error) throw new Error(`fetchClusterStats failed: ${error.message}`);
  if (!data) return null;
  return {
    dataset_id: data.dataset_id,
    cluster_id: data.cluster_id,
    cell_count: data.cell_count,
    pct: data.pct,
    markers: parseMarkers(data.markers),
  };
}

/** Every cluster's markers in a dataset, by cluster id; a cluster with no stats
 *  row is absent. */
export async function fetchClusterMarkers(
  datasetId: number,
): Promise<Map<string, ClusterMarkers | null>> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_cluster_stats")
    .select("cluster_id, markers")
    .eq("dataset_id", datasetId);
  if (error) throw new Error(`fetchClusterMarkers failed: ${error.message}`);
  return new Map((data ?? []).map((row) => [row.cluster_id, parseMarkers(row.markers)]));
}

/** What the other side of a one-vs-rest comparison is called. */
export const REST_GROUP = "rest";

/** How many markers the panel lists. */
export const TOP_MARKERS = 25;

/** A marker is up in its cluster and below this FDR. */
export const MARKER_FDR_CUT = 0.05;

/** A cluster's one-vs-rest comparison in the dataset's latest analysis. */
export type OneVsRest = { deId: number; contrast: string | null };

/** This cluster's comparison against the rest in the newest analysis that has one; when
 *  it has several (e.g. one per library), the one over the most cells. Null when none does. */
export async function findOneVsRest(
  datasetId: number,
  clusterId: string,
): Promise<OneVsRest | null> {
  const supabase = createClientSupabaseClient();
  const { data: runs, error: runError } = await supabase
    .from("scrna_de_runs")
    .select("id")
    .eq("dataset_id", datasetId)
    .eq("status", "complete")
    .order("completed_at", { ascending: false });
  if (runError) throw new Error(`findOneVsRest failed: ${runError.message}`);
  const runIds = (runs ?? []).map((r) => r.id);
  if (runIds.length === 0) return null;

  const { data, error } = await supabase
    .from("scrna_de")
    .select("id, run_id, contrast, n_group1, n_group2")
    .in("run_id", runIds)
    .eq("cluster_id", clusterId)
    .eq("group_kind", "cluster")
    .eq("group1", clusterId)
    .eq("group2", REST_GROUP)
    .eq("tested", true);
  if (error) throw new Error(`findOneVsRest failed: ${error.message}`);
  const cells = (r: { n_group1: number | null; n_group2: number | null }) =>
    (r.n_group1 ?? 0) + (r.n_group2 ?? 0);
  const rows = data ?? [];
  const newestRun = runIds.find((id) => rows.some((r) => r.run_id === id));
  const best = rows.filter((r) => r.run_id === newestRun).sort((a, b) => cells(b) - cells(a))[0];
  return best ? { deId: best.id, contrast: best.contrast } : null;
}

/** A comparison's top markers, strongest first, and how many genes are markers at all:
 *  higher in the cluster, below the FDR cut. */
export async function fetchDeMarkers(deId: number): Promise<ClusterMarkers> {
  const supabase = createClientSupabaseClient();
  const [top, count] = await Promise.all([
    supabase
      .from("scrna_de_genes")
      .select("log2fc, fdr, pct_1, pct_2, scrna_genes!inner(gene_name)")
      .eq("de_id", deId)
      .gt("log2fc", 0)
      .lt("fdr", MARKER_FDR_CUT)
      .order("fdr", { ascending: true })
      .order("log2fc", { ascending: false })
      .limit(TOP_MARKERS),
    supabase
      .from("scrna_de_genes")
      .select("id", { count: "exact", head: true })
      .eq("de_id", deId)
      .gt("log2fc", 0)
      .lt("fdr", MARKER_FDR_CUT),
  ]);
  if (top.error) throw new Error(`fetchDeMarkers failed: ${top.error.message}`);
  if (count.error) throw new Error(`fetchDeMarkers failed: ${count.error.message}`);
  type Row = { log2fc: number | null; fdr: number; pct_1: number | null; pct_2: number | null;
    scrna_genes: { gene_name: string } | null };
  return {
    top: ((top.data ?? []) as unknown as Row[]).map((r) => ({
      gene: r.scrna_genes?.gene_name ?? "",
      log2fc: r.log2fc ?? 0,
      q: r.fdr,
      pct_1: r.pct_1 ?? 0,
      pct_2: r.pct_2 ?? 0,
    })),
    n_significant: count.count ?? 0,
  };
}
