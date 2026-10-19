/**
 * A dataset's GO and KEGG pathway enrichment: the newest ready run in scrna_enrichment_runs,
 * and its file (bloom-enrichment/1, gzipped JSON in the scrna bucket), checked on load.
 */

import { gunzipSync, strFromU8 } from "fflate";

import type { Client } from "./de-types";

export const ENRICHMENT_FORMAT = "bloom-enrichment/1";
export const ENRICHMENT_BUCKET = "scrna";
export const DIRECTIONS = ["up", "down"] as const;
export const KINDS = ["GO_BP", "GO_MF", "GO_CC", "KEGG"] as const;
export type Direction = (typeof DIRECTIONS)[number];
export type TermKind = (typeof KINDS)[number];

export const KIND_LABELS: Record<TermKind, string> = {
  GO_BP: "GO biological process",
  GO_MF: "GO molecular function",
  GO_CC: "GO cellular component",
  KEGG: "KEGG pathways",
};

// gzip's first two bytes.
const GZIP_MAGIC = [0x1f, 0x8b];

export interface EnrichmentTerm {
  comparison: string;
  direction: Direction;
  kind: TermKind;
  term: string;
  name: string;
  list_genes_in_term: number;
  list_size: number;
  term_size: number;
  background_size: number;
  p: number;
  fdr: number;
  genes: string[];
}

export interface EnrichmentFile {
  format: typeof ENRICHMENT_FORMAT;
  dataset_id: number;
  de_run_id: number;
  annotation: string;
  params: Record<string, number>;
  background_genes: number;
  annotated_genes: number;
  results: EnrichmentTerm[];
}

export interface EnrichmentRun {
  id: number;
  de_run_id: number;
  annotation_build_id: number;
  file_path: string;
  ready_at: string | null;
}

/** A file that isn't a bloom-enrichment/1 file of this dataset. */
export class EnrichmentFileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EnrichmentFileError";
  }
}

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isCount = (v: unknown): v is number => isNumber(v) && Number.isInteger(v) && v >= 0;

function checkTerm(t: Record<string, unknown>, i: number): EnrichmentTerm {
  const fail = (why: string) => {
    throw new EnrichmentFileError(`result ${i} (${String(t.term)}): ${why}`);
  };
  if (typeof t.comparison !== "string" || typeof t.term !== "string" ||
      typeof t.name !== "string") fail("missing its comparison, term or name");
  if (!DIRECTIONS.includes(t.direction as Direction)) fail(`unknown direction ${String(t.direction)}`);
  if (!KINDS.includes(t.kind as TermKind)) fail(`unknown kind ${String(t.kind)}`);
  for (const key of ["list_genes_in_term", "list_size", "term_size", "background_size"]) {
    if (!isCount(t[key])) fail(`${key} is not a count`);
  }
  if (!isNumber(t.p) || !isNumber(t.fdr)) fail("p or fdr is not a number");
  if (!Array.isArray(t.genes) || !t.genes.every((g) => typeof g === "string")) {
    fail("genes is not a list of names");
  }
  if ((t.list_genes_in_term as number) > (t.list_size as number) ||
      (t.list_genes_in_term as number) > (t.term_size as number)) {
    fail("has more list genes in the term than in the list or the term");
  }
  return t as unknown as EnrichmentTerm;
}

/** An enrichment file's bytes (gzipped, or already unzipped by the server), checked. */
export function decodeEnrichment(bytes: Uint8Array, datasetId: number): EnrichmentFile {
  const zipped = bytes[0] === GZIP_MAGIC[0] && bytes[1] === GZIP_MAGIC[1];
  let doc: Record<string, unknown>;
  try {
    doc = JSON.parse(strFromU8(zipped ? gunzipSync(bytes) : bytes));
  } catch {
    throw new EnrichmentFileError("the enrichment file is not readable JSON");
  }
  if (doc.format !== ENRICHMENT_FORMAT) {
    throw new EnrichmentFileError(`the file is ${String(doc.format)}, not ${ENRICHMENT_FORMAT}`);
  }
  if (doc.dataset_id !== datasetId) {
    throw new EnrichmentFileError(`the file is for dataset ${String(doc.dataset_id)}`);
  }
  if (!Array.isArray(doc.results)) throw new EnrichmentFileError("the file has no results");
  const results = doc.results.map((t, i) => checkTerm(t as Record<string, unknown>, i));
  return { ...(doc as unknown as EnrichmentFile), results };
}

/** The dataset's newest ready enrichment run and its file, or null when it has none. */
export async function fetchLatestEnrichment(
  supabase: Client,
  datasetId: number,
): Promise<{ run: EnrichmentRun; file: EnrichmentFile } | null> {
  const { data, error } = await supabase
    .from("scrna_enrichment_runs")
    .select("id, de_run_id, annotation_build_id, file_path, ready_at")
    .eq("dataset_id", datasetId)
    .eq("status", "ready")
    .order("ready_at", { ascending: false })
    .limit(1);
  if (error) throw new Error(error.message);
  const run = data?.[0] as EnrichmentRun | undefined;
  if (!run) return null;
  const stored = await supabase.storage.from(ENRICHMENT_BUCKET).download(run.file_path);
  if (stored.error || !stored.data) {
    throw new Error(stored.error?.message ?? `couldn't read ${run.file_path}`);
  }
  const bytes = new Uint8Array(await stored.data.arrayBuffer());
  return { run, file: decodeEnrichment(bytes, datasetId) };
}

const clusterNumber = (comparison: string) => {
  const n = Number.parseInt(comparison, 10);
  return Number.isNaN(n) ? Number.POSITIVE_INFINITY : n;
};

/** The file's comparisons, in cluster order (2 before 10), then by name. */
export function comparisonsOf(file: EnrichmentFile): string[] {
  return [...new Set(file.results.map((t) => t.comparison))].sort(
    (a, b) => clusterNumber(a) - clusterNumber(b) || a.localeCompare(b),
  );
}

/** One comparison, direction and kind's terms, most significant first. */
export function termsFor(file: EnrichmentFile, comparison: string, direction: Direction,
                         kind: TermKind): EnrichmentTerm[] {
  return file.results
    .filter((t) => t.comparison === comparison && t.direction === direction && t.kind === kind)
    .sort((a, b) => a.fdr - b.fdr || a.term.localeCompare(b.term));
}

/** The share of the list that falls in the term. */
export function geneRatio(term: EnrichmentTerm): number {
  return term.list_size === 0 ? 0 : term.list_genes_in_term / term.list_size;
}
