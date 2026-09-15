import type { Client, Cuts, DeEntry } from "./de-types";
import { groupNames, isTested, passes } from "./de-types";

/** Rows per request. Reading stops at the first empty page, so a server-side
 *  row cap cannot cut the list short. */
export const SUMMARY_PAGE_ROWS = 1000;

/** Past this many passing results a tally would count only some of them, so
 *  the summary shows none and asks for tighter cuts. */
export const SUMMARY_MAX_RESULTS = 5000;

/** Comparison ids per request, to keep the URL short. */
export const IDS_PER_REQUEST = 200;

/** A gene result that passes the cuts. A missing percentage is NaN. */
export type PassingResult = {
  deId: number;
  geneId: number;
  gene: string;
  log2fc: number;
  pvalue: number;
  fdr: number;
  pct1: number;
  pct2: number;
};

/** A passing result with its comparison, and the group it is higher in. */
export type Place = {
  result: PassingResult;
  entry: DeEntry;
  inFirstGroup: boolean;
  higherIn: string;
};

export type GeneTally = {
  geneId: number;
  gene: string;
  cellTypes: number;
  results: number;
  bestFdr: number;
  /** Cell types per group, when one contrast is picked; null when pooled,
   *  since "up" names a different group in each contrast. */
  higher: { group: string; cellTypes: number }[] | null;
  places: Place[];
};

export type GridCell = {
  entry: DeEntry;
  tested: boolean;
  counts: { group: string; n: number }[];
};

/** A stored gene result, as read. An infinite fold change arrives as text. */
type Row = {
  de_id: number;
  gene_id: number;
  log2fc: number | string | null;
  pvalue: number;
  fdr: number;
  pct_1: number | null;
  pct_2: number | null;
  scrna_genes: { gene_name: string } | null;
};

/** What a contrast is called in the summary: its two groups. */
export function comparisonLabel(entry: DeEntry): string {
  return entry.group1 && entry.group2 ? `${entry.group1} vs ${entry.group2}` : "against the rest";
}

/** `selection` is a contrast, or null for every contrast. */
const inSelection = (entry: DeEntry, selection: string | null) =>
  selection === null || (entry.contrast ?? "") === selection;

function toResult(row: Row): PassingResult {
  return {
    deId: row.de_id,
    geneId: row.gene_id,
    gene: row.scrna_genes?.gene_name ?? `gene ${row.gene_id}`,
    log2fc: row.log2fc === null ? NaN : Number(row.log2fc),
    pvalue: row.pvalue,
    fdr: row.fdr,
    pct1: row.pct_1 ?? NaN,
    pct2: row.pct_2 ?? NaN,
  };
}

/** Every result of the tested comparisons that passes the cuts, strongest
 *  first, or `truncated` when more than SUMMARY_MAX_RESULTS pass. */
export async function fetchPassingResults(
  client: Client,
  comparisons: DeEntry[],
  cuts: Cuts,
): Promise<{ results: PassingResult[]; truncated: boolean }> {
  // The database orders NaN above every number, so `fdr < NaN` would match every row.
  if (!Number.isFinite(cuts.fdr) || !Number.isFinite(cuts.log2fc)) {
    throw new Error("Both cuts must be numbers.");
  }
  const ids = comparisons.filter(isTested).map((entry) => entry.id);
  const results: PassingResult[] = [];
  for (let i = 0; i < ids.length; i += IDS_PER_REQUEST) {
    const chunk = ids.slice(i, i + IDS_PER_REQUEST);
    for (let start = 0; ; start += SUMMARY_PAGE_ROWS) {
      const { data, error } = await client
        .from("scrna_de_genes")
        .select("de_id, gene_id, log2fc, pvalue, fdr, pct_1, pct_2, scrna_genes!inner(gene_name)")
        .in("de_id", chunk)
        .lt("fdr", cuts.fdr)
        .or(`log2fc.gt.${cuts.log2fc},log2fc.lt.${-cuts.log2fc}`)
        .order("fdr", { ascending: true })
        .order("id", { ascending: true })
        .range(start, start + SUMMARY_PAGE_ROWS - 1);
      if (error) throw new Error(error.message);
      const rows = (data ?? []) as unknown as Row[];
      if (rows.length === 0) break;
      for (const row of rows) {
        const result = toResult(row);
        if (passes(result.fdr, result.log2fc, cuts)) results.push(result);
      }
      if (results.length > SUMMARY_MAX_RESULTS) return { results: [], truncated: true };
    }
  }
  return { results, truncated: false };
}

/** The passing results in the selection, each with its comparison and the
 *  group it is higher in, by FDR and then gene name. */
export function listResults(
  results: PassingResult[],
  comparisons: DeEntry[],
  selection: string | null,
): Place[] {
  const byId = new Map(comparisons.map((entry) => [entry.id, entry]));
  const places: Place[] = [];
  for (const result of results) {
    const entry = byId.get(result.deId);
    if (!entry || !inSelection(entry, selection)) continue;
    const { a, b } = groupNames(entry);
    const inFirstGroup = result.log2fc > 0;
    places.push({ result, entry, inFirstGroup, higherIn: inFirstGroup ? a : b });
  }
  return places.sort(
    (x, y) =>
      x.result.fdr - y.result.fdr ||
      x.result.gene.localeCompare(y.result.gene) ||
      x.result.geneId - y.result.geneId ||
      x.entry.id - y.entry.id,
  );
}

/** Cell types with at least one tested comparison in the selection. */
function cellTypesTested(comparisons: DeEntry[], selection: string | null): number {
  return new Set(
    comparisons
      .filter((entry) => isTested(entry) && inSelection(entry, selection))
      .map((entry) => entry.cluster_id ?? ""),
  ).size;
}

/** One row per gene, by the cell types it passes in, then its lowest FDR,
 *  then its name. `places` must come from `listResults` for the same selection. */
export function tallyGenes(
  places: Place[],
  comparisons: DeEntry[],
  selection: string | null,
): { genes: GeneTally[]; cellTypesTested: number } {
  const byGene = new Map<number, Place[]>();
  for (const place of places) {
    const list = byGene.get(place.result.geneId) ?? [];
    list.push(place);
    byGene.set(place.result.geneId, list);
  }
  const genes: GeneTally[] = [];
  for (const [geneId, own] of byGene) {
    const cells = (keep: (p: Place) => boolean) =>
      new Set(own.filter(keep).map((p) => p.entry.cluster_id ?? "")).size;
    const { a, b } = groupNames(own[0].entry);
    genes.push({
      geneId,
      gene: own[0].result.gene,
      cellTypes: cells(() => true),
      results: own.length,
      bestFdr: Math.min(...own.map((p) => p.result.fdr)),
      higher: selection === null
        ? null
        : [
            { group: a, cellTypes: cells((p) => p.inFirstGroup) },
            { group: b, cellTypes: cells((p) => !p.inFirstGroup) },
          ],
      places: own,
    });
  }
  genes.sort(
    (x, y) =>
      y.cellTypes - x.cellTypes ||
      x.bestFdr - y.bestFdr ||
      x.gene.localeCompare(y.gene) ||
      x.geneId - y.geneId,
  );
  return { genes, cellTypesTested: cellTypesTested(comparisons, selection) };
}

/** The header's numbers for the selection. */
export function summaryTotals(
  places: Place[],
  comparisons: DeEntry[],
  selection: string | null,
): { genes: number; results: number; comparisonsWithResults: number; comparisonsTested: number } {
  return {
    genes: new Set(places.map((p) => p.result.geneId)).size,
    results: places.length,
    comparisonsWithResults: new Set(places.map((p) => p.entry.id)).size,
    comparisonsTested: comparisons.filter(
      (entry) => isTested(entry) && inSelection(entry, selection),
    ).length,
  };
}

/** A cell type by contrast grid of passing counts per group. A cell is null
 *  where the cell type has no such comparison. */
export function countByCellType(
  places: Place[],
  comparisons: DeEntry[],
  selection: string | null,
): { columns: { contrast: string; label: string }[]; rows: { cellType: string; cells: (GridCell | null)[] }[] } {
  const shown = comparisons.filter((entry) => inSelection(entry, selection));
  const columns: { contrast: string; label: string }[] = [];
  const cellTypes: string[] = [];
  for (const entry of shown) {
    const contrast = entry.contrast ?? "";
    if (!columns.some((c) => c.contrast === contrast)) {
      columns.push({ contrast, label: comparisonLabel(entry) });
    }
    const cellType = entry.cluster_id ?? "";
    if (!cellTypes.includes(cellType)) cellTypes.push(cellType);
  }
  const rows = cellTypes.map((cellType) => ({
    cellType,
    cells: columns.map(({ contrast }): GridCell | null => {
      const entry = shown.find(
        (e) => (e.cluster_id ?? "") === cellType && (e.contrast ?? "") === contrast,
      );
      if (!entry) return null;
      if (!isTested(entry)) return { entry, tested: false, counts: [] };
      const own = places.filter((p) => p.entry.id === entry.id);
      const { a, b } = groupNames(entry);
      return {
        entry,
        tested: true,
        counts: [
          { group: a, n: own.filter((p) => p.inFirstGroup).length },
          { group: b, n: own.filter((p) => !p.inFirstGroup).length },
        ],
      };
    }),
  }));
  return { columns, rows };
}
