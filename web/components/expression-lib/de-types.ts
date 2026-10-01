import type { Database } from "@/lib/database.types";
import type { createClientSupabaseClient } from "@/lib/supabase/client";

/** A comparison in the dataset's analysis: a row of scrna_de.
 *
 * `tested` false means it was considered and skipped; the group sizes on the row
 * are what explain why, so it is shown rather than hidden. A null `contrast` is
 * an older one-vs-rest row, which has one selector and no groups to name.
 */
export type DeEntry = {
  id: number;
  cluster_id: string | null;
  contrast: string | null;
  group1: string | null;
  group2: string | null;
  n_group1: number | null;
  n_group2: number | null;
  n_genes_tested: number | null;
  tested: boolean | null;
};

/** The analysis the comparisons belong to: a row of scrna_de_runs. */
export type AnalysisRun = {
  id: number;
  method: string;
  completed_at: string | null;
  params: Database["public"]["Tables"]["scrna_de_runs"]["Row"]["params"];
};

export type Client = ReturnType<typeof createClientSupabaseClient>;

/** The significance cuts: FDR below one, |log2FC| above the other. */
export type Cuts = { fdr: number; log2fc: number };

/** The cuts the analysis itself applied, so the panel agrees with the counts
 *  stored on the row rather than quietly using a stricter rule of its own. */
export const DEFAULT_FDR_CUT = 0.05;
export const DEFAULT_LOG2FC_CUT = 0.5;

/** Higher in a comparison's first group, and higher in its second. */
export const FIRST_GROUP_COLOUR = "#c62828";
export const SECOND_GROUP_COLOUR = "#1565c0";

/** The two sides of this comparison, named. One answer, so the chart, the
 *  table and the tooltip cannot disagree about which group is which. */
export function groupNames(entry: DeEntry | null): { a: string; b: string } {
  return entry?.group1 && entry.group2
    ? { a: entry.group1, b: entry.group2 }
    : { a: entry?.cluster_id ?? "this cell type", b: "the rest" };
}

/** A result passes when its FDR is below the FDR cut and its fold change is
 *  beyond the fold-change cut, both strictly. No fold change, no direction. */
export function passes(fdr: number, log2fc: number, cuts: Cuts): boolean {
  if (Number.isNaN(log2fc)) return false;
  return fdr < cuts.fdr && Math.abs(log2fc) > cuts.log2fc;
}

/** Whether a comparison has gene results. An older one-vs-rest row carries no
 *  `tested` flag and was tested. */
export function isTested(entry: DeEntry): boolean {
  return entry.tested !== false && entry.n_genes_tested !== 0;
}

/** A fold change for display. An infinite one means the gene was found in one
 *  group only. */
export function formatFoldChange(value: number, digits: number): string {
  if (Number.isFinite(value)) return value.toFixed(digits);
  if (Number.isNaN(value)) return "";
  return value > 0 ? "+∞" : "−∞";
}
