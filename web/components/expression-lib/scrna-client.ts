/**
 * Typed fetch helpers for the Expression UMAP view.
 *
 * Uses the shared Supabase client for PostgREST calls (RLS + auth
 * headers handled automatically) and a raw fetch for per-gene `.bin`
 * downloads from object storage. The storage base URL is resolved from
 * NEXT_PUBLIC_STORAGE_URL with a dev default of http://localhost:9100.
 */

import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/database.types";

type Dataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];
type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

const DEFAULT_STORAGE_URL = "http://localhost:9100";
const STORAGE_BUCKET = "scrna";
const GENE_SEARCH_DEFAULT_LIMIT = 20;

/** The shape of a row returned by the `scrna_cell_arrays` RPC. */
export interface CellArraysRow {
  x: number;
  y: number;
  pc1: number | null;
  pc2: number | null;
  pc3: number | null;
  pc4: number | null;
  pc5: number | null;
  cluster_ordinal: number;
}

/**
 * Resolve the storage base URL for `.bin` fetches. Reads from
 * NEXT_PUBLIC_STORAGE_URL at call time (not module load) so tests
 * can override via `process.env` per-test.
 */
export function getStorageBaseUrl(): string {
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

/** Fetch a single dataset row, or null if the id is not found. */
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

/**
 * Resolve the first non-deleted scRNA dataset for a species slug. Matches the
 * existing expression-scatterplot behavior: single-dataset-per-species for now.
 * Returns null when no dataset exists for that species.
 */
export async function fetchDatasetBySpeciesCommonName(
  commonName: string,
): Promise<Dataset | null> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_datasets")
    .select("*, species!inner(common_name)")
    .eq("species.common_name", commonName)
    .is("deleted_at", null)
    .order("id", { ascending: true })
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(`fetchDatasetBySpecies failed: ${error.message}`);
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

/**
 * Fetch all per-cell arrays for a dataset in a single RPC call.
 * Replaces the legacy 1000-row `.range()` pagination loop.
 */
export async function fetchCells(datasetId: number): Promise<CellArraysRow[]> {
  const supabase = createClientSupabaseClient();
  // The RPC is typed in database.types.ts; call generically.
  const { data, error } = await (supabase.rpc as unknown as (
    fn: string,
    args: Record<string, unknown>,
  ) => Promise<{ data: CellArraysRow[] | null; error: { message: string } | null }>)(
    "scrna_cell_arrays",
    { ds_id: datasetId },
  );
  if (error) throw new Error(`fetchCells failed: ${error.message}`);
  return (data as CellArraysRow[]) ?? [];
}

/**
 * Prefix-search gene names in a dataset via the trigram-indexed RPC.
 * Debounce on the caller side; this function does no debouncing.
 */
export async function searchGenes(
  datasetId: number,
  q: string,
  limit: number = GENE_SEARCH_DEFAULT_LIMIT,
): Promise<string[]> {
  if (!q) return [];
  const supabase = createClientSupabaseClient();
  const { data, error } = await (supabase.rpc as unknown as (
    fn: string,
    args: Record<string, unknown>,
  ) => Promise<{
    data: { gene_name: string }[] | null;
    error: { message: string } | null;
  }>)("scrna_gene_search", { ds_id: datasetId, q, lim: limit });
  if (error) throw new Error(`searchGenes failed: ${error.message}`);
  return (data ?? []).map((row) => row.gene_name);
}

/**
 * Download the per-gene Float32 expression sidecar from object storage and
 * decode it as a Float32Array. Values are log-normalized (Seurat NormalizeData
 * or equivalent) and MUST be used unmodified downstream — the colorbar and
 * shader perform range mapping at render time, not here.
 *
 * Uses the Supabase storage client (not a raw fetch against MinIO) so the
 * anon JWT is sent as a header — the `scrna` bucket is authenticated-read,
 * not public-read, and raw MinIO returns 403 for anonymous GETs.
 */
export async function fetchGeneBin(
  datasetName: string,
  geneName: string,
): Promise<Float32Array> {
  const supabase = createClientSupabaseClient();
  const objectPath = `counts/${datasetName}/${geneName}.bin`;
  const { data, error } = await supabase.storage
    .from(STORAGE_BUCKET)
    .download(objectPath);
  if (error) {
    throw new Error(`fetchGeneBin failed for ${geneName}: ${error.message}`);
  }
  const buf = await data.arrayBuffer();
  return new Float32Array(buf);
}
