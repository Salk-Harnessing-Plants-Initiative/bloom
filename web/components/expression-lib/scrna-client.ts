/** Typed fetch helpers for the Expression UMAP view. */

import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

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

/** Download one gene's expression, as a value per cell in `cell_number` order.
 *
 * The object path comes from `scrna_counts`, never from the dataset and gene
 * names: the loader that wrote production stored paths its own way, so
 * `pennycress_data2.json` lives under `counts/pennycress_data2_1_/`. Building
 * the path here would miss every object already in the platform.
 *
 * The stored file is sparse -- it names only the cells where the gene is
 * expressed, since single-cell data is mostly zeros -- and comes in two shapes,
 * both of which are in the bucket today:
 *
 *     {"248": 1.62, "267": 1.69}        one object, keyed by cell index
 *     [{"248": 1.62}, {"267": 1.69}]    one object per cell, in a list
 *
 * The list is the older of the two: the loader was changed to write a single
 * object and the files already stored were never converted. Both are read here,
 * so a bucket holding a mixture works, and converting the old ones is something
 * that can happen later, partially, or never.
 *
 * Either way it is expanded into the dense array the plot needs, zero-filled,
 * so callers see one value per cell whether it was stored or not.
 *
 * Read through the storage client rather than a bare URL, because the bucket is
 * private and every other private asset is read the same way.
 *
 * The object carries no cell identifiers -- the index is the identity -- which
 * is why the ingest refuses to write unless the database already holds these
 * cells in this order.
 */
export async function fetchGeneCounts(
  datasetId: number,
  geneName: string,
  cellCount: number,
): Promise<Float32Array> {
  const supabase = createClientSupabaseClient();

  const { data: row, error: rowError } = await supabase
    .from("scrna_counts")
    .select("counts_object_path, scrna_genes!inner(gene_name)")
    .eq("dataset_id", datasetId)
    .eq("scrna_genes.gene_name", geneName)
    .maybeSingle();
  if (rowError) {
    throw new Error(`fetchGeneCounts failed for ${geneName}: ${rowError.message}`);
  }
  const path = (row as { counts_object_path: string | null } | null)
    ?.counts_object_path;
  // Not an error to report as a failure: most datasets in the platform have
  // genes registered whose object was never written.
  if (!path) {
    throw new Error(
      `fetchGeneCounts: ${geneName} has no stored expression in this dataset`,
    );
  }

  const { data, error } = await supabase.storage
    .from(STORAGE_BUCKET)
    .download(path);
  if (error || !data) {
    throw new Error(
      `fetchGeneCounts failed for ${geneName}: ${error?.message ?? "no data"}`,
    );
  }
  const parsed = JSON.parse(await data.text()) as
    | Record<string, number>
    | Record<string, number>[];
  let sparse: Record<string, number>;
  if (Array.isArray(parsed)) {
    // Merged rather than spread: these run to tens of thousands of entries,
    // past what a spread can pass as arguments.
    sparse = {};
    for (const entry of parsed) Object.assign(sparse, entry);
  } else {
    sparse = parsed;
  }

  const out = new Float32Array(cellCount);
  for (const [index, value] of Object.entries(sparse)) {
    const i = Number(index);
    // A key outside the dataset is a pairing error, not a value to drop
    // quietly: the object was written against a different set of cells.
    if (!Number.isInteger(i) || i < 0 || i >= cellCount) {
      throw new Error(
        `fetchGeneCounts: ${geneName} names cell ${index}, but the dataset ` +
          `holds ${cellCount} cells`,
      );
    }
    out[i] = value;
  }
  return out;
}
