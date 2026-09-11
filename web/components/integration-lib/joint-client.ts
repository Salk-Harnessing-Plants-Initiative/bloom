/** Reads for the joint embedding map: its points, one label, and a clicked point. */

import { createClientSupabaseClient } from "@/lib/supabase/client";

export interface JointArrays {
  x: number[];
  y: number[];
  /** Which member dataset each point belongs to, by member ordinal. */
  memberOrdinals: number[];
}

/** Every point's position and dataset, in point order; null when the map has no
 *  finished points to show. */
export async function fetchJointArrays(embeddingId: number): Promise<JointArrays | null> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.rpc("scrna_embedding_arrays", { emb_id: embeddingId });
  if (error) throw new Error(`Could not load the map's cells: ${error.message}`);
  const row = data?.[0];
  if (!row) return null;
  return { x: row.x, y: row.y, memberOrdinals: row.member_ordinal };
}

/** One label for every point: its values, and each point's index into them. */
export async function fetchLabelCodes(
  embeddingId: number,
  key: string,
): Promise<{ levels: string[]; codes: number[] }> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.rpc("scrna_embedding_label_codes", {
    emb_id: embeddingId,
    label_key: key,
  });
  if (error) throw new Error(`Could not load ${key}: ${error.message}`);
  const row = data?.[0];
  return { levels: row?.levels ?? [], codes: row?.codes ?? [] };
}

export interface PointRecord {
  barcode: string;
  datasetId: number;
  cellId: number | null;
}

/** The index-th point in point order. The arrays carry no barcodes, so a
 *  clicked point's record is read on its own. */
export async function fetchPoint(embeddingId: number, index: number): Promise<PointRecord | null> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase
    .from("scrna_embedding_points")
    .select("barcode, dataset_id, cell_id")
    .eq("embedding_id", embeddingId)
    .order("ordinal")
    .range(index, index);
  if (error) throw new Error(`Could not load the cell: ${error.message}`);
  const row = data?.[0];
  return row ? { barcode: row.barcode, datasetId: row.dataset_id, cellId: row.cell_id } : null;
}
