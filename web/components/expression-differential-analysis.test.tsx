/**
 * The differential expression panel shows one analysis's comparisons between two
 * groups. Two things about it are easy to get wrong and impossible to notice
 * from a screenshot: which way round the fold change reads, and whether the
 * numbers on screen belong to the comparison currently selected.
 *
 * The comparisons and their gene results come from the database, so the panel
 * is driven against a stand-in client that answers each table.
 */

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import Panel, {
  beforeDepthMatching,
  columnsFor,
  countSignificant,
  directionLabel,
  testedLabel,
  toGeneData,
} from "./expression-differential-analysis";

afterEach(cleanup);

const twoGroup = {
  id: 1,
  cluster_id: "Cortex",
  contrast: "pFACT_vs_Col-0",
  group1: "pFACT",
  group2: "Col-0",
  n_group1: 245,
  n_group2: 172,
  n_genes_tested: 15430,
  tested: true,
};

const neverRun = { ...twoGroup, id: 3, n_group1: 3, n_group2: 172, n_genes_tested: 0, tested: false };

/** An older row, from before comparisons named two groups. */
const oneVsRest = {
  id: 9,
  cluster_id: "Cortex",
  contrast: null,
  group1: null,
  group2: null,
  n_group1: null,
  n_group2: null,
  n_genes_tested: null,
  tested: null,
};

const RUN = {
  id: 5,
  method: "scanpy-wilcoxon",
  completed_at: "2026-09-11T00:00:00Z",
  params: { notes: { cells_before_depth_matching: {
    "Cortex / pFACT_vs_Col-0": { pFACT: 300, "Col-0": 200 },
  } } },
};

describe("directionLabel", () => {
  it("states the direction as a whole sentence, leading word included", () => {
    expect(directionLabel(twoGroup)).toBe(
      "A positive fold change is higher in pFACT than in Col-0; " +
      "a negative one is higher in Col-0.",
    );
  });

  it("does not invent group names for a one-vs-rest comparison", () => {
    const label = directionLabel(oneVsRest);
    expect(label).toContain("this cell type");
    expect(label).not.toContain("null");
  });

  it("reads the groups from the row, not from a fixed order", () => {
    const flipped = { ...twoGroup, group1: "Col-0", group2: "pHORST" };
    expect(directionLabel(flipped)).toContain("higher in Col-0 than in pHORST");
  });
});

describe("testedLabel", () => {
  it("reports how many genes were tested, from the row, before any gene is read", () => {
    expect(testedLabel(twoGroup)).toBe("15,430 genes tested");
  });

  it("says a comparison was not tested rather than showing zero", () => {
    expect(testedLabel(neverRun)).toBe("not tested");
  });

  it("says nothing when the row carries no count", () => {
    expect(testedLabel(oneVsRest)).toBe("");
  });
});

describe("beforeDepthMatching", () => {
  it("names each group's cells before depth matching, from the analysis notes", () => {
    expect(beforeDepthMatching(RUN, twoGroup)).toBe("pFACT 300, Col-0 200");
  });

  it("says nothing when the notes do not record the comparison", () => {
    expect(beforeDepthMatching(RUN, { ...twoGroup, contrast: "pHORST_vs_Col-0", group1: "pHORST" }))
      .toBe("");
    expect(beforeDepthMatching({ ...RUN, params: {} }, twoGroup)).toBe("");
    expect(beforeDepthMatching(null, twoGroup)).toBe("");
  });
});

describe("toGeneData", () => {
  const row = {
    log2fc: 1.5, pvalue: 0.001, fdr: 0.01, pct_1: 0.6, pct_2: 0.2,
    scrna_genes: { gene_name: "AT1G01010" },
  };

  it("reads a stored gene result in the chart's shape", () => {
    expect(toGeneData(row)).toEqual({
      gene: "AT1G01010", _row: "AT1G01010", avg_log2FC: 1.5, p_val: 0.001,
      p_val_adj: 0.01, "pct.1": 0.6, "pct.2": 0.2,
    });
  });

  it("reads no fold change as NaN, and an infinite one sent as text as a number", () => {
    expect(toGeneData({ ...row, log2fc: null }).avg_log2FC).toBeNaN();
    expect(toGeneData({ ...row, log2fc: "Infinity" }).avg_log2FC).toBe(Infinity);
    expect(toGeneData({ ...row, log2fc: "-Infinity" }).avg_log2FC).toBe(-Infinity);
  });
});

// --------------------------------------------------------------------------- //
// The panel itself, rendered
// --------------------------------------------------------------------------- //

const ROWS = [
  twoGroup,
  { ...twoGroup, id: 2, contrast: "pHORST_vs_Col-0", group1: "pHORST" },
  { ...twoGroup, id: 4, cluster_id: "Phloem" },
  { ...neverRun, cluster_id: "Xylem" },
];

/** A comparison whose genes all fall short of the cuts. */
const NOTHING_SIGNIFICANT = 4;

/** Releases each comparison's genes only when the test says so, and records
 *  every query the panel makes. */
const gate: {
  release: Record<number, () => void>;
  order: number[];
  queries: { table: string; filters: Record<string, unknown> }[];
} = { release: {}, order: [], queries: [] };

function answer(table: string, filters: Record<string, unknown>, start: number): Promise<unknown> {
  if (table === "scrna_de_runs") return Promise.resolve({ data: [RUN], error: null });
  if (table === "scrna_de") return Promise.resolve({ data: ROWS, error: null });
  if (start > 0) return Promise.resolve({ data: [], error: null });
  const deId = filters.de_id as number;
  return new Promise((resolve) => {
    gate.order.push(deId);
    gate.release[deId] = () =>
      resolve({
        data: [{ log2fc: 2, pvalue: 0.01, fdr: deId === NOTHING_SIGNIFICANT ? 0.9 : 0.01,
                 pct_1: 0.5, pct_2: 0.1, scrna_genes: { gene_name: `gene-of-${deId}` } }],
        error: null,
      });
  });
}

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: (table: string) => {
      const filters: Record<string, unknown> = {};
      let start = 0;
      const query = {
        select: () => query,
        eq: (column: string, value: unknown) => {
          filters[column] = value;
          return query;
        },
        order: () => query,
        limit: () => query,
        range: (from: number) => {
          start = from;
          return query;
        },
        then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) => {
          gate.queries.push({ table, filters: { ...filters } });
          return answer(table, filters, start).then(resolve, reject);
        },
      };
      return query;
    },
  }),
}));

describe("the panel", () => {
  // Shared across tests, so a later one could otherwise satisfy its own wait
  // against the previous test's residue and race its own render.
  beforeEach(() => {
    gate.order.length = 0;
    gate.release = {};
    gate.queries.length = 0;
  });

  it("reads the dataset's latest complete analysis and that analysis's comparisons", async () => {
    render(<Panel file_id={7} />);
    await waitFor(() => expect(gate.order).toContain(1));
    expect(gate.queries.find((q) => q.table === "scrna_de_runs")?.filters)
      .toEqual({ dataset_id: 7, status: "complete" });
    expect(gate.queries.find((q) => q.table === "scrna_de")?.filters).toEqual({ run_id: 5 });
  });

  it("ignores an answer that lands after the comparison changed", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(1));

    fireEvent.mouseDown(screen.getByLabelText("Comparison"));
    fireEvent.click(await screen.findByRole("option", { name: /pHORST_vs_Col-0/ }));
    await waitFor(() => expect(gate.order).toContain(2));

    // The one now on screen answers first, then the abandoned one answers.
    gate.release[2]();
    await screen.findAllByText("gene-of-2");

    await act(async () => {
      gate.release[1]();
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });

    expect(screen.queryByText("gene-of-1")).toBeNull();
    expect(screen.queryAllByText("gene-of-2").length).toBeGreaterThan(0);
  });

  it("names both groups, their sizes, and the counts before depth matching", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(1));
    gate.release[1]();

    expect(
      await screen.findByText(/pFACT \(245 cells\) against Col-0 \(172 cells\), in Cortex/),
    ).toBeTruthy();
    expect(screen.getByText(/A positive fold change is higher in pFACT than in Col-0/))
      .toBeTruthy();
    expect(screen.getByText(/Before it: pFACT 300, Col-0 200/)).toBeTruthy();
  });

  it("says so when no gene passes the cuts, rather than leave a grey plot unexplained", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(1));

    fireEvent.mouseDown(screen.getByLabelText("Cell type"));
    fireEvent.click(await screen.findByRole("option", { name: "Phloem" }));
    await waitFor(() => expect(gate.order).toContain(NOTHING_SIGNIFICANT));
    gate.release[NOTHING_SIGNIFICANT]();

    expect(await screen.findByText(/so every point is grey/)).toBeTruthy();
  });

  it("does not say so when some genes pass", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(1));
    gate.release[1]();
    await screen.findAllByText("gene-of-1");
    expect(screen.queryByText(/so every point is grey/)).toBeNull();
  });

  it("says a comparison was never run, with the sizes that explain why", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(1));

    fireEvent.mouseDown(screen.getByLabelText("Cell type"));
    fireEvent.click(await screen.findByRole("option", { name: "Xylem" }));

    expect(await screen.findByText(/This comparison was not run/)).toBeTruthy();
    expect(screen.getByText(/too few on one side to compare/)).toBeTruthy();
    expect(screen.getByText(/3 cells in pFACT/)).toBeTruthy();
  });
});

describe("the gene table's columns", () => {
  const headerOf = (entry: Parameters<typeof columnsFor>[0], field: string) =>
    columnsFor(entry).find((c) => c.field === field)?.headerName;

  it("heads each per-group column with the group it belongs to", () => {
    expect(headerOf(twoGroup, "pct.1")).toBe("% in pFACT");
    expect(headerOf(twoGroup, "pct.2")).toBe("% in Col-0");
  });

  it("falls back to cell type and rest when the row names no groups", () => {
    expect(headerOf(oneVsRest, "pct.1")).toBe("% in Cortex");
    expect(headerOf(oneVsRest, "pct.2")).toBe("% in the rest");
  });
});

describe("countSignificant", () => {
  const rows = [
    { p_val_adj: 0.01, avg_log2FC: 2.0 },
    { p_val_adj: 0.01, avg_log2FC: 0.7 },
    { p_val_adj: 0.01, avg_log2FC: -0.7 },
    { p_val_adj: 0.01, avg_log2FC: -3.0 },
    { p_val_adj: 0.20, avg_log2FC: 5.0 },
  ];

  it("tells the two directions apart", () => {
    expect(
      countSignificant(
        [
          { p_val_adj: 0.001, avg_log2FC: 2 },
          { p_val_adj: 0.001, avg_log2FC: 1.5 },
          { p_val_adj: 0.001, avg_log2FC: -2 },
        ],
        0.05,
        0.5,
      ),
    ).toEqual({ up: 2, down: 1, total: 3 });
  });

  it("counts at the cuts the analysis used", () => {
    expect(countSignificant(rows, 0.05, 0.5)).toEqual({ up: 2, down: 2, total: 4 });
  });

  it("a stricter fold-change cut drops the genes between the two", () => {
    expect(countSignificant(rows, 0.05, 1.0)).toEqual({ up: 1, down: 1, total: 2 });
  });

  it("treats the cuts as strict, so a gene exactly on one does not pass", () => {
    expect(countSignificant([{ p_val_adj: 0.05, avg_log2FC: 2 }], 0.05, 0.5).total).toBe(0);
    expect(countSignificant([{ p_val_adj: 0.01, avg_log2FC: 0.5 }], 0.05, 0.5).total).toBe(0);
  });

  it("leaves out a gene with no fold change, which has no direction", () => {
    expect(countSignificant([{ p_val_adj: 0.001, avg_log2FC: NaN }], 0.05, 0.5))
      .toEqual({ up: 0, down: 0, total: 0 });
  });

  it("counts an infinite fold change in its direction", () => {
    expect(countSignificant([{ p_val_adj: 0.001, avg_log2FC: Infinity }], 0.05, 0.5))
      .toEqual({ up: 1, down: 0, total: 1 });
  });
});
