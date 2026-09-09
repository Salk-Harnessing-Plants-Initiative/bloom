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

/** One exportable DE result for a cluster.
 *
 * `contrast` is null for the cluster-vs-rest marker list, and names the
 * comparison otherwise (e.g. "pFACT_vs_Col-0"). A dataset may hold either
 * kind, or both, for the same cluster.
 *
 * `group1` and `group2` are the two sides of that comparison. Labels are built
 * from them rather than by splitting `contrast`, so no naming convention is
 * assumed of the pipeline that produced it.
 */
export type DeExport = {
  filePath: string;
  contrast: string | null;
  group1: string | null;
  group2: string | null;
};

/**
 * Fetch every DE result this cluster has a file for.
 *
 * Returns the cluster-vs-rest marker list first, then any two-group contrasts
 * by name. Rows recording a comparison that was never run carry no file and
 * are omitted — there is nothing to export.
 */
export async function fetchDeExports(
  datasetId: number,
  clusterId: string,
): Promise<DeExport[]> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_de")
    .select("file_path, contrast, group1, group2")
    .eq("dataset_id", datasetId)
    .eq("cluster_id", clusterId)
    .not("file_path", "is", null)
    .order("contrast", { ascending: true, nullsFirst: true });
  if (error) throw new Error(`fetchDeExports failed: ${error.message}`);
  return (data ?? []).map((row) => ({
    filePath: row.file_path as string,
    contrast: row.contrast,
    group1: row.group1,
    group2: row.group2,
  }));
}
