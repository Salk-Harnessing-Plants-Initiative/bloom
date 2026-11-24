/** Typed fetch helpers for the Expression UMAP view. */

import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/database.types";

import { readGeneCounts } from "./gene-counts";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const GENE_SEARCH_DEFAULT_LIMIT = 20;

/** Row returned by the `scrna_cell_arrays` RPC.
 *
 * `cluster_ordinal === 255` is the orphan sentinel: the cell's `cluster_id`
 * does not appear in `scrna_clusters` for this dataset. Real clusters are
 * 0..254 (CHECK constraint on `scrna_clusters.ordinal`).
 */
export interface CellArraysRow {
  x: number;
  y: number;
  cluster_ordinal: number;
  /** The sample this cell came from, or null for datasets that record none. */
  replicate: string | null;
  /** The cell's genotype, or null when none was loaded. */
  genotype?: string | null;
  /** Labels the map can filter on, such as transgene status; null when none were loaded. */
  facets?: Record<string, string> | null;
}

/** Cluster ordinal returned by the RPC for cells with no matching catalog row. */
export const ORPHAN_CLUSTER_ORDINAL = 255;

/** Fetch a single dataset row, or null if not found. */
export async function fetchDataset(datasetId: number): Promise<Dataset | null> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_datasets")
    .select("*")
    .eq("id", datasetId)
    .maybeSingle();
  if (error) throw new Error(`fetchDataset failed: ${error.message}`);
  return (data as Dataset | null) ?? null;
}

/** Fetch the cluster catalog for a dataset, ordered by ordinal ascending. */
export async function fetchClusters(datasetId: number): Promise<Cluster[]> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_clusters")
    .select("*")
    .eq("dataset_id", datasetId)
    .order("ordinal", { ascending: true });
  if (error) throw new Error(`fetchClusters failed: ${error.message}`);
  return (data as Cluster[]) ?? [];
}

/** Fetch all per-cell arrays for a dataset in a single RPC call. */
export async function fetchCells(datasetId: number): Promise<CellArraysRow[]> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.rpc("scrna_cell_arrays", {
    ds_id: datasetId,
  });
  if (error) throw new Error(`fetchCells failed: ${error.message}`);
  return (data as CellArraysRow[]) ?? [];
}

/** Prefix-search gene names in a dataset. Caller is responsible for debouncing. */
export async function searchGenes(
  datasetId: number,
  q: string,
  limit: number = GENE_SEARCH_DEFAULT_LIMIT,
): Promise<string[]> {
  if (!q) return [];
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.rpc("scrna_gene_search", {
    ds_id: datasetId,
    q,
    lim: limit,
  });
  if (error) throw new Error(`searchGenes failed: ${error.message}`);
  return (data ?? []).map((row) => row.gene_name);
}

export { NoStoredExpressionError } from "./gene-counts";

/** Download one gene's expression with the browser's client; see `readGeneCounts`. */
export async function fetchGeneCounts(
  datasetId: number,
  geneName: string,
  cellCount: number,
): Promise<Float32Array> {
  return readGeneCounts(createClientSupabaseClient(), datasetId, geneName, cellCount);
}
