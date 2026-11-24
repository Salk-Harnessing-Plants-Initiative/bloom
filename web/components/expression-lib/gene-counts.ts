/** One gene's expression per cell, read from its count file with the caller's Supabase client, so
 * the dataset pages (browser client) and the server (viewer's client) read it the same way. */

import type { SupabaseClient } from "@supabase/supabase-js";

const STORAGE_BUCKET = "scrna";

/** Count files larger than this are refused, as bloommcp refuses them. */
export const MAX_COUNT_FILE_BYTES = 16 * 1024 * 1024;

/** A dataset stores no expression for this gene; most datasets have many such genes. */
export class NoStoredExpressionError extends Error {
  constructor(readonly gene: string) {
    super(`fetchGeneCounts: ${gene} has no stored expression in this dataset`);
    this.name = "NoStoredExpressionError";
  }
}

/** Download one gene's expression, as a value per cell in `cell_number` order.
 *
 * The object path comes from `scrna_counts`, never from the dataset and gene names: the loader
 * that wrote production stored paths its own way. The file is sparse and comes in two shapes,
 * both in the bucket today: `{"248": 1.62}` and the older `[{"248": 1.62}]`. It is expanded into
 * a zero-filled dense array. A value that isn't a finite number, a cell outside the dataset, a
 * file over 16 MB or invalid JSON is refused with an error naming the gene.
 */
export async function readGeneCounts(
  supabase: SupabaseClient,
  datasetId: number,
  geneName: string,
  cellCount: number,
): Promise<Float32Array> {
  const { data: row, error: rowError } = await supabase
    .from("scrna_counts")
    .select("counts_object_path, scrna_genes!inner(gene_name)")
    .eq("dataset_id", datasetId)
    .eq("scrna_genes.gene_name", geneName)
    .maybeSingle();
  if (rowError) throw new Error(`fetchGeneCounts failed for ${geneName}: ${rowError.message}`);
  const path = (row as { counts_object_path: string | null } | null)?.counts_object_path;
  // Not a failure: most datasets have genes registered whose object was never written.
  if (!path) throw new NoStoredExpressionError(geneName);

  const { data, error } = await supabase.storage.from(STORAGE_BUCKET).download(path);
  if (error || !data) throw new Error(`fetchGeneCounts failed for ${geneName}: ${error?.message ?? "no data"}`);
  if (data.size > MAX_COUNT_FILE_BYTES) throw new Error(`${geneName}'s count file is over 16 MB.`);

  let parsed: unknown;
  try {
    parsed = JSON.parse(await data.text());
  } catch {
    throw new Error(`${geneName}'s count file is not valid JSON.`);
  }
  const entries = Array.isArray(parsed) ? parsed : [parsed];
  const out = new Float32Array(cellCount);
  for (const entry of entries) {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`${geneName}'s count file is not a map of cells to values.`);
    }
    // Looped rather than spread: these run to tens of thousands of entries.
    for (const [index, value] of Object.entries(entry as Record<string, unknown>)) {
      const i = Number(index);
      // A key outside the dataset means the file was written against other cells.
      if (!Number.isInteger(i) || i < 0 || i >= cellCount) {
        throw new Error(`fetchGeneCounts: ${geneName} names cell ${index}, but the dataset holds ${cellCount} cells`);
      }
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new Error(`${geneName}'s count file holds a value that is not a number, for cell ${index}.`);
      }
      out[i] = value;
    }
  }
  return out;
}
