/**
 * The summary answers "which genes should I look at across cell types" from
 * the results that pass the cuts. What is pinned here is what a scientist
 * would read off it: which results count as passing, which group each is
 * higher in, how genes are ranked, what the denominators are, and that the
 * summary asks the database only for passing rows and says so when there are
 * too many to summarise.
 */

import { describe, expect, it } from "vitest";

import type { Client, DeEntry } from "./de-types";
import { passes } from "./de-types";
import {
  IDS_PER_REQUEST,
  SUMMARY_MAX_RESULTS,
  SUMMARY_PAGE_ROWS,
  type PassingResult,
  comparisonLabel,
  countByCellType,
  fetchPassingResults,
  listResults,
  summaryTotals,
  tallyGenes,
} from "./de-summary";

const CUTS = { fdr: 0.05, log2fc: 0.5 };

const entry = (
  id: number,
  cluster: string,
  contrast: string | null,
  tested: boolean | null = true,
): DeEntry => {
  const [group1, group2] = contrast ? contrast.split("_vs_") : [null, null];
  return {
    id,
    cluster_id: cluster,
    contrast,
    group1,
    group2,
    n_group1: 100,
    n_group2: 90,
    n_genes_tested: tested === false ? 0 : 15000,
    tested,
  };
};

/** Four cell types, two contrasts. Xylem's second was never tested, and
 *  Phloem was only compared one way. */
const COMPARISONS: DeEntry[] = [
  entry(1, "Cortex", "pFACT_vs_Col-0"),
  entry(2, "Cortex", "pHORST_vs_Col-0"),
  entry(3, "Pericycle", "pFACT_vs_Col-0"),
  entry(4, "Pericycle", "pHORST_vs_Col-0"),
  entry(5, "Xylem", "pFACT_vs_Col-0"),
  entry(6, "Xylem", "pHORST_vs_Col-0", false),
  entry(7, "Phloem", "pFACT_vs_Col-0"),
];

const result = (
  deId: number,
  geneId: number,
  log2fc: number,
  fdr: number,
  gene = `G${geneId}`,
): PassingResult => ({
  deId,
  geneId,
  gene,
  log2fc,
  pvalue: fdr / 10,
  fdr,
  pct1: 0.5,
  pct2: 0.2,
});

describe("passes", () => {
  it("is strict on both cuts", () => {
    expect(passes(0.05, 1, CUTS)).toBe(false);
    expect(passes(0.01, 0.5, CUTS)).toBe(false);
    expect(passes(0.01, -0.5, CUTS)).toBe(false);
    expect(passes(0.0499, 0.51, CUTS)).toBe(true);
    expect(passes(0.0499, -0.51, CUTS)).toBe(true);
  });

  it("never passes a result with no fold change", () => {
    expect(passes(0.001, NaN, CUTS)).toBe(false);
  });

  it("passes an infinite fold change in its direction", () => {
    expect(passes(0.001, Infinity, CUTS)).toBe(true);
    expect(passes(0.001, -Infinity, CUTS)).toBe(true);
  });
});

describe("comparisonLabel", () => {
  it("names a comparison by its two groups", () => {
    expect(comparisonLabel(COMPARISONS[0])).toBe("pFACT vs Col-0");
  });

  it("calls an older one-vs-rest comparison what it is", () => {
    expect(comparisonLabel(entry(9, "Cortex", null, null))).toBe("against the rest");
  });
});

describe("listResults", () => {
  it("sorts by FDR, then gene name, and names the group each result is higher in", () => {
    const rows = listResults(
      [result(3, 1, -1, 0.01, "b"), result(1, 2, 2, 0.001), result(3, 3, 1, 0.01, "a")],
      COMPARISONS,
      null,
    );
    expect(rows.map((r) => r.result.gene)).toEqual(["G2", "a", "b"]);
    expect(rows.map((r) => r.higherIn)).toEqual(["pFACT", "pFACT", "Col-0"]);
    expect(rows[0].entry.cluster_id).toBe("Cortex");
  });

  it("keeps only the picked comparison", () => {
    const rows = listResults(
      [result(1, 1, 1, 0.01), result(2, 1, 1, 0.01)],
      COMPARISONS,
      "pHORST_vs_Col-0",
    );
    expect(rows.map((r) => r.entry.id)).toEqual([2]);
  });

  it("reads an older one-vs-rest result as higher in the cell type or in the rest", () => {
    const old = [entry(9, "Cortex", null, null)];
    const rows = listResults([result(9, 1, 1, 0.01), result(9, 2, -1, 0.02)], old, null);
    expect(rows.map((r) => r.higherIn)).toEqual(["Cortex", "the rest"]);
  });

  it("drops a result whose comparison is not in the analysis shown", () => {
    expect(listResults([result(99, 1, 1, 0.01)], COMPARISONS, null)).toEqual([]);
  });
});

describe("tallyGenes", () => {
  it("ranks by cell types, then lowest FDR, then name", () => {
    const places = listResults(
      [
        // A: 3 cell types, best 1e-4
        result(1, 10, 1, 1e-4, "A"), result(3, 10, 1, 1e-3, "A"), result(5, 10, 1, 1e-3, "A"),
        // B: 3 cell types, best 1e-9
        result(1, 11, 1, 1e-9, "B"), result(3, 11, 1, 1e-3, "B"), result(7, 11, 1, 1e-3, "B"),
        // C: 4 cell types, weaker
        result(1, 12, 1, 0.01, "C"), result(3, 12, 1, 0.01, "C"),
        result(5, 12, 1, 0.01, "C"), result(7, 12, 1, 0.01, "C"),
      ],
      COMPARISONS,
      null,
    );
    const { genes } = tallyGenes(places, COMPARISONS, null);
    expect(genes.map((g) => g.gene)).toEqual(["C", "B", "A"]);
    expect(genes.map((g) => g.cellTypes)).toEqual([4, 3, 3]);
    expect(genes[1].bestFdr).toBe(1e-9);
  });

  it("counts a cell type once when a gene passes two of its comparisons", () => {
    const places = listResults(
      [result(1, 10, 1, 0.01), result(2, 10, -1, 0.02)],
      COMPARISONS,
      null,
    );
    const [gene] = tallyGenes(places, COMPARISONS, null).genes;
    expect(gene.cellTypes).toBe(1);
    expect(gene.results).toBe(2);
    expect(gene.places.map((p) => p.entry.id)).toEqual([1, 2]);
  });

  it("names directions by group when one comparison is picked", () => {
    const places = listResults(
      [result(1, 10, 1, 0.01), result(3, 10, 2, 0.01), result(5, 10, -1, 0.01)],
      COMPARISONS,
      "pFACT_vs_Col-0",
    );
    const [gene] = tallyGenes(places, COMPARISONS, "pFACT_vs_Col-0").genes;
    expect(gene.higher).toEqual([
      { group: "pFACT", cellTypes: 2 },
      { group: "Col-0", cellTypes: 1 },
    ]);
  });

  it("does not pool directions across comparisons, where up means a different group", () => {
    const places = listResults([result(1, 10, 1, 0.01)], COMPARISONS, null);
    expect(tallyGenes(places, COMPARISONS, null).genes[0].higher).toBeNull();
  });

  it("counts as the denominator only cell types with a tested comparison in the selection", () => {
    expect(tallyGenes([], COMPARISONS, null).cellTypesTested).toBe(4);
    // Xylem's pHORST comparison was never tested and Phloem has none.
    expect(tallyGenes([], COMPARISONS, "pHORST_vs_Col-0").cellTypesTested).toBe(2);
  });

  it("keeps two catalogue genes with the same name apart", () => {
    const places = listResults(
      [result(1, 10, 1, 0.01, "DUP"), result(3, 11, 1, 0.01, "DUP")],
      COMPARISONS,
      null,
    );
    expect(tallyGenes(places, COMPARISONS, null).genes.map((g) => g.geneId).sort())
      .toEqual([10, 11]);
  });
});

describe("summaryTotals", () => {
  it("counts genes, results, and tested comparisons with and without a passing result", () => {
    const places = listResults(
      [result(1, 10, 1, 0.01), result(3, 10, 1, 0.01), result(3, 11, -1, 0.01)],
      COMPARISONS,
      null,
    );
    expect(summaryTotals(places, COMPARISONS, null)).toEqual({
      genes: 2,
      results: 3,
      comparisonsWithResults: 2,
      comparisonsTested: 6,
    });
    expect(summaryTotals([], COMPARISONS, "pHORST_vs_Col-0").comparisonsTested).toBe(2);
  });
});

describe("countByCellType", () => {
  const places = listResults(
    [result(1, 10, 1, 0.01), result(1, 11, 1, 0.01), result(1, 12, -1, 0.01)],
    COMPARISONS,
    null,
  );

  it("has one column per comparison, and one when a comparison is picked", () => {
    expect(countByCellType(places, COMPARISONS, null).columns.map((c) => c.label))
      .toEqual(["pFACT vs Col-0", "pHORST vs Col-0"]);
    expect(countByCellType(places, COMPARISONS, "pHORST_vs_Col-0").columns.map((c) => c.label))
      .toEqual(["pHORST vs Col-0"]);
  });

  it("gives each cell the counts per group, not tested, zero, or nothing", () => {
    const { rows } = countByCellType(places, COMPARISONS, null);
    expect(rows.map((r) => r.cellType)).toEqual(["Cortex", "Pericycle", "Xylem", "Phloem"]);
    const [cortex, pericycle, xylem, phloem] = rows;
    expect(cortex.cells[0]).toMatchObject({
      tested: true,
      counts: [{ group: "pFACT", n: 2 }, { group: "Col-0", n: 1 }],
    });
    expect(pericycle.cells[0]).toMatchObject({
      tested: true,
      counts: [{ group: "pFACT", n: 0 }, { group: "Col-0", n: 0 }],
    });
    expect(xylem.cells[1]).toMatchObject({ tested: false });
    expect(phloem.cells[1]).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// Reading from the database
// --------------------------------------------------------------------------- //

type Call = {
  table: string;
  select?: string;
  in?: [string, unknown[]];
  lt?: [string, unknown];
  or?: string;
  order: [string, boolean][];
  range?: [number, number];
};

type Row = Record<string, unknown>;

/** A stand-in client that records each request and answers it with `answer`. */
function recordingClient(answer: (call: Call) => { data: Row[] | null; error: { message: string } | null }) {
  const calls: Call[] = [];
  const client = {
    from(table: string) {
      const call: Call = { table, order: [] };
      const query = {
        select(columns: string) { call.select = columns; return query; },
        in(column: string, values: unknown[]) { call.in = [column, values]; return query; },
        lt(column: string, value: unknown) { call.lt = [column, value]; return query; },
        or(filter: string) { call.or = filter; return query; },
        order(column: string, options?: { ascending?: boolean }) {
          call.order.push([column, options?.ascending !== false]);
          return query;
        },
        range(from: number, to: number) { call.range = [from, to]; return query; },
        then(resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) {
          calls.push(call);
          return Promise.resolve(answer(call)).then(resolve, reject);
        },
      };
      return query;
    },
  };
  return { client: client as unknown as Client, calls };
}

const row = (deId: number, geneId: number, log2fc: number | string, fdr = 0.01): Row => ({
  de_id: deId,
  gene_id: geneId,
  log2fc,
  pvalue: fdr / 10,
  fdr,
  pct_1: 0.5,
  pct_2: null,
  scrna_genes: { gene_name: `G${geneId}` },
});

/** Answers `total` rows, a page at a time, then an empty page. */
const rowsInPages = (total: number) => (call: Call) => {
  const [from, to] = call.range ?? [0, total - 1];
  const count = Math.max(0, Math.min(total, to + 1) - from);
  return {
    data: Array.from({ length: count }, (_, i) => row(1, from + i + 1, 1)),
    error: null,
  };
};

describe("fetchPassingResults", () => {
  it("asks only for passing rows of the tested comparisons, strongest first", async () => {
    const { client, calls } = recordingClient(() => ({ data: [], error: null }));
    await fetchPassingResults(client, COMPARISONS, CUTS);

    expect(calls[0].table).toBe("scrna_de_genes");
    expect(calls[0].select).toContain("scrna_genes!inner(gene_name)");
    expect(calls[0].in).toEqual(["de_id", [1, 2, 3, 4, 5, 7]]);
    expect(calls[0].lt).toEqual(["fdr", 0.05]);
    expect(calls[0].or).toBe("log2fc.gt.0.5,log2fc.lt.-0.5");
    expect(calls[0].order).toEqual([["fdr", true], ["id", true]]);
    expect(calls[0].range).toEqual([0, SUMMARY_PAGE_ROWS - 1]);
  });

  it("reads page after page until an empty one", async () => {
    const { client, calls } = recordingClient(rowsInPages(1500));
    const { results, truncated } = await fetchPassingResults(client, COMPARISONS, CUTS);
    expect(results).toHaveLength(1500);
    expect(truncated).toBe(false);
    expect(calls.map((c) => c.range)).toEqual([[0, 999], [1000, 1999], [2000, 2999]]);
  });

  it("summarises exactly the cap, and refuses one more", async () => {
    const atCap = await fetchPassingResults(
      recordingClient(rowsInPages(SUMMARY_MAX_RESULTS)).client, COMPARISONS, CUTS);
    expect(atCap).toMatchObject({ truncated: false });
    expect(atCap.results).toHaveLength(SUMMARY_MAX_RESULTS);

    const past = await fetchPassingResults(
      recordingClient(rowsInPages(SUMMARY_MAX_RESULTS + 1)).client, COMPARISONS, CUTS);
    expect(past).toEqual({ results: [], truncated: true });
  });

  it("splits a long list of comparisons across requests", async () => {
    const many = Array.from({ length: 450 }, (_, i) => entry(i + 1, `C${i}`, "a_vs_b"));
    const { client, calls } = recordingClient(() => ({ data: [], error: null }));
    await fetchPassingResults(client, many, CUTS);
    expect(calls.map((c) => (c.in?.[1] as unknown[]).length))
      .toEqual([IDS_PER_REQUEST, IDS_PER_REQUEST, 50]);
  });

  it("reads an infinite fold change sent as text, and a missing percentage as NaN", async () => {
    const { client } = recordingClient((call) => ({
      data: call.range?.[0] === 0 ? [row(1, 1, "Infinity"), row(1, 2, "-Infinity")] : [],
      error: null,
    }));
    const { results } = await fetchPassingResults(client, COMPARISONS, CUTS);
    expect(results.map((r) => r.log2fc)).toEqual([Infinity, -Infinity]);
    expect(results[0].pct1).toBe(0.5);
    expect(results[0].pct2).toBeNaN();
    expect(results[0].gene).toBe("G1");
  });

  it("keeps only rows that pass the shared rule, whatever the server returns", async () => {
    const { client } = recordingClient((call) => ({
      data: call.range?.[0] === 0 ? [row(1, 1, 1, 0.05), row(1, 2, 0.5), row(1, 3, 2)] : [],
      error: null,
    }));
    const { results } = await fetchPassingResults(client, COMPARISONS, CUTS);
    expect(results.map((r) => r.geneId)).toEqual([3]);
  });

  it("throws the database's message", async () => {
    const { client } = recordingClient(() => ({ data: null, error: { message: "boom" } }));
    await expect(fetchPassingResults(client, COMPARISONS, CUTS)).rejects.toThrow("boom");
  });

  it("asks for nothing when no comparison was tested", async () => {
    const { client, calls } = recordingClient(() => ({ data: [], error: null }));
    const out = await fetchPassingResults(client, [entry(6, "Xylem", "a_vs_b", false)], CUTS);
    expect(out).toEqual({ results: [], truncated: false });
    expect(calls).toHaveLength(0);
  });

  it("counts an older one-vs-rest row, which carries no tested flag, as tested", async () => {
    const { client, calls } = recordingClient(() => ({ data: [], error: null }));
    await fetchPassingResults(client, [entry(9, "Cortex", null, null)], CUTS);
    expect(calls[0].in).toEqual(["de_id", [9]]);
  });

  it("throws when a later page fails, rather than return the first page as complete", async () => {
    const { client } = recordingClient((call) =>
      call.range?.[0] === 0 ? rowsInPages(1500)(call) : { data: null, error: { message: "timeout" } });
    await expect(fetchPassingResults(client, COMPARISONS, CUTS)).rejects.toThrow("timeout");
  });

  it("counts the cap across requests for different comparisons", async () => {
    const many = Array.from({ length: 250 }, (_, i) => entry(i + 1, `C${i}`, "a_vs_b"));
    const { client } = recordingClient((call) =>
      rowsInPages((call.in?.[1] as unknown[]).length === IDS_PER_REQUEST ? 3000 : 2001)(call));
    expect(await fetchPassingResults(client, many, CUTS)).toEqual({ results: [], truncated: true });
  });

  it("refuses cuts that are not numbers, and asks for nothing", async () => {
    const { client, calls } = recordingClient(() => ({ data: [], error: null }));
    await expect(fetchPassingResults(client, COMPARISONS, { fdr: NaN, log2fc: 0.5 }))
      .rejects.toThrow("Both cuts must be numbers.");
    await expect(fetchPassingResults(client, COMPARISONS, { fdr: 0.05, log2fc: Infinity }))
      .rejects.toThrow("Both cuts must be numbers.");
    expect(calls).toHaveLength(0);
  });
});

describe("infinite fold changes", () => {
  it("are higher in the first group when positive and the second when negative", () => {
    const rows = listResults(
      [result(1, 1, Infinity, 0.01), result(1, 2, -Infinity, 0.02)],
      COMPARISONS,
      null,
    );
    expect(rows.map((r) => r.higherIn)).toEqual(["pFACT", "Col-0"]);
  });
});
