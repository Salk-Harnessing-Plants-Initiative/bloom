/** One gene's expression per cell, read from its count file with the caller's Supabase client, so
 * the dataset pages (browser client) and the server (viewer's client) read it the same way. Its
 * checks match bloommcp's (bloommcp/src/bloom_mcp/data_access/expression_counts.py). Messages
 * leave the gene out: callers name it. */

import type { SupabaseClient } from "@supabase/supabase-js";

const STORAGE_BUCKET = "scrna";

/** Count files larger than this are refused, as bloommcp refuses them (prod's largest ~1 MB). */
export const MAX_COUNT_FILE_BYTES = 16 * 1024 * 1024;

/** Longest cell key shown in a message; a damaged file's key can be any length. */
const MAX_SHOWN_KEY = 20;
/** Longest stored path read, and characters that could take a path out of the bucket. */
const MAX_PATH_CHARS = 1024;
const UNSAFE_PATH_CHARS = /[%?#:\\]/;
const MB = 1024 * 1024;

/** A dataset stores no expression for this gene; most datasets have many such genes. */
export class NoStoredExpressionError extends Error {
  constructor(readonly gene: string) {
    super(`${gene} has no stored expression in this dataset.`);
    this.name = "NoStoredExpressionError";
  }
}

/** Why a stored path can't be read, or null when it stays inside its bucket. */
function pathProblem(path: string): string | null {
  if (!path || path !== path.trim()) return "is empty or has spaces at an end";
  if (path.length > MAX_PATH_CHARS) return `is longer than ${MAX_PATH_CHARS} characters`;
  if (path.startsWith("/") || UNSAFE_PATH_CHARS.test(path)) return "has a character that could leave the bucket";
  if (path.split("/").some((part) => part === "" || part === "." || part === "..")) {
    return "has an empty, '.' or '..' folder";
  }
  // eslint-disable-next-line no-control-regex -- control characters are what this looks for
  if (/[\u0000-\u001f\u007f]/.test(path)) return "has a control character";
  return null;
}

const shown = (key: string) => JSON.stringify(key.length > MAX_SHOWN_KEY ? `${key.slice(0, MAX_SHOWN_KEY)}…` : key);

const isMissing = (error: { message?: string; statusCode?: string; status?: number }) =>
  error.statusCode === "404" || error.status === 404 || /not found/i.test(error.message ?? "");

const isMap = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);

/** Download one gene's expression, as a value per cell in `cell_number` order.
 *
 * The object path comes from `scrna_counts`, never from the dataset and gene names: the loader
 * that wrote production stored paths its own way. The file is sparse and comes in two shapes,
 * both in the bucket today: `{"248": 1.62}` and the older `[{"248": 1.62}]`, whose entries are
 * merged first (a later entry wins). It is expanded into a zero-filled dense array.
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
  if (rowError) throw new Error("Its count record could not be read; try again.", { cause: rowError });
  const path = (row as { counts_object_path: string | null } | null)?.counts_object_path;
  // Not a failure: most datasets have genes registered whose object was never written.
  if (!path) throw new NoStoredExpressionError(geneName);
  const problem = pathProblem(path);
  if (problem) throw new Error(`Its stored file path ${problem}, so it was not read.`);

  const bucket = supabase.storage.from(STORAGE_BUCKET);
  const storageFailure = (error: { message?: string }) =>
    new Error(isMissing(error) ? "Its count file is missing." : "Its count file could not be read; try again.", {
      cause: error,
    });
  const tooBig = (size: number) =>
    new Error(`Its count file is ${Math.round(size / MB)} MB, over the ${MAX_COUNT_FILE_BYTES / MB} MB limit.`);

  // Size first, so an oversized file is never downloaded; checked again below in case it changed.
  const { data: info, error: infoError } = await bucket.info(path);
  if (infoError) throw storageFailure(infoError);
  const listedSize = info?.size ?? 0;
  if (listedSize > MAX_COUNT_FILE_BYTES) throw tooBig(listedSize);
  const { data, error } = await bucket.download(path);
  if (error || !data) throw storageFailure(error ?? {});
  if (data.size > MAX_COUNT_FILE_BYTES) throw tooBig(data.size);

  let parsed: unknown;
  try {
    parsed = JSON.parse(await data.text());
  } catch {
    throw new Error("Its count file is not valid JSON.");
  }
  if (!(isMap(parsed) || (Array.isArray(parsed) && parsed.every(isMap)))) {
    throw new Error("Its count file is not a map of cells to values.");
  }
  // Looped rather than spread or assigned: these run to tens of thousands of entries.
  const sparse = new Map<string, unknown>();
  for (const entry of Array.isArray(parsed) ? parsed : [parsed]) {
    for (const [key, value] of Object.entries(entry)) sparse.set(key, value);
  }

  const out = new Float32Array(cellCount);
  const maxDigits = String(cellCount).length;
  for (const [key, value] of sparse) {
    // Digits only, as bloommcp reads them; a key outside the dataset means other cells.
    const digits = key.replace(/^0+(?=\d)/, "");
    const i = /^\d+$/.test(key) && digits.length <= maxDigits ? Number(digits) : -1;
    if (i < 0 || i >= cellCount) {
      throw new Error(`Its count file names cell ${shown(key)}, but the dataset holds ${cellCount} cells.`);
    }
    out[i] = typeof value === "number" ? value : Number.NaN;
    // Checked after storing, so a value too large for a cell's float also counts as not finite.
    if (typeof value !== "number" || !Number.isFinite(out[i])) {
      throw new Error(`Its count file's value for cell ${key} is not a finite number.`);
    }
  }
  return out;
}
