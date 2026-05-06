/** Typed fetch helpers for the Expression UMAP view. */

import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const DEFAULT_STORAGE_URL = "http://localhost:9100";
const STORAGE_BUCKET = "scrna";
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
}

/** Cluster ordinal returned by the RPC for cells with no matching catalog row. */
export const ORPHAN_CLUSTER_ORDINAL = 255;

/** Storage base URL for `.bin` fetches, from NEXT_PUBLIC_STORAGE_URL. */
function getStorageBaseUrl(): string {
  const fromEnv =
    typeof process !== "undefined" ? process.env?.NEXT_PUBLIC_STORAGE_URL : undefined;
  if (fromEnv && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  if (typeof console !== "undefined") {
    console.warn(
      `[scrna-client] NEXT_PUBLIC_STORAGE_URL unset; defaulting to ${DEFAULT_STORAGE_URL}`,
    );
  }
  return DEFAULT_STORAGE_URL;
}

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

/** Download a per-gene expression vector as a Float32Array. */
export async function fetchGeneBin(
  datasetName: string,
  geneName: string,
): Promise<Float32Array> {
  const base = getStorageBaseUrl();
  const url = `${base}/${STORAGE_BUCKET}/counts/${encodeURIComponent(datasetName)}/${encodeURIComponent(geneName)}.bin`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`fetchGeneBin failed for ${geneName}: HTTP ${res.status}`);
  }
  const buf = await res.arrayBuffer();
  return new Float32Array(buf);
}
