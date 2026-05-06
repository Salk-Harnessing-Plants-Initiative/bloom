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

/**
 * Fetch whether this cluster has a DE CSV file to export.
 * Returns null when no scrna_de row exists for this (dataset, cluster).
 */
export async function fetchDeFilePath(
  datasetId: number,
  clusterId: string,
): Promise<string | null> {
  const supabase = createClientSupabaseClient();
  const { data } = await supabase
    .from("scrna_de")
    .select("file_path")
    .eq("dataset_id", datasetId)
    .eq("cluster_id", clusterId)
    .maybeSingle();
  return data?.file_path ?? null;
}
