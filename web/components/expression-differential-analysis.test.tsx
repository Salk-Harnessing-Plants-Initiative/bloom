/**
 * The differential expression panel shows a comparison between two groups. Two
 * things about it are easy to get wrong and impossible to notice from a
 * screenshot: which way round the fold change reads, and whether the numbers on
 * screen belong to the comparison currently selected.
 *
 * These cover the labels that answer the first, and the counts that come from
 * the row rather than from the file — so a reader sees how much was significant
 * before waiting for two megabytes of it.
 */

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import Panel, {
  columnsFor,
  countSignificant,
  directionLabel,
  significanceLabel,
} from "./expression-differential-analysis";

afterEach(cleanup);

const twoGroup = {
  cluster_id: "Cortex",
  file_path: "de/d/Cortex__pFACT_vs_Col-0.json",
  contrast: "pFACT_vs_Col-0",
  group1: "pFACT",
  group2: "Col-0",
  n_group1: 245,
  n_group2: 172,
  n_genes_tested: 15430,
  n_significant_fdr_lfc: 7,
};

const neverRun = {
  ...twoGroup,
  file_path: null,
  n_group1: 3,
  n_group2: 172,
  n_genes_tested: 0,
  n_significant_fdr_lfc: 0,
};

/** An older row, from before comparisons named two groups. */
const oneVsRest = {
  cluster_id: "Cortex",
  file_path: "de/d/Cortex.json",
  contrast: null,
  group1: null,
  group2: null,
  n_group1: null,
  n_group2: null,
  n_genes_tested: null,
  n_significant_fdr_lfc: null,
};

describe("directionLabel", () => {
  it("states the direction as a whole sentence, leading word included", () => {
    expect(directionLabel(twoGroup)).toBe(
      "A positive fold change is higher in pFACT than in Col-0; " +
      "a negative one is higher in Col-0.",
    );
  });

  it("says which group a positive fold change is higher in", () => {
    const label = directionLabel(twoGroup);
    expect(label).toContain("higher in pFACT than in Col-0");
    expect(label).toContain("negative one is higher in Col-0");
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

describe("significanceLabel", () => {
  it("reports significant of tested from the row, before any file is read", () => {
    expect(significanceLabel(twoGroup)).toBe("7 of 15,430 significant");
  });

  it("says a comparison was not tested rather than showing zero of zero", () => {
    expect(significanceLabel(neverRun)).toBe("not tested");
  });

  it("says nothing at all when the row carries no counts", () => {
    expect(significanceLabel(oneVsRest)).toBe("");
  });

  it("groups the thousands, because these run to five figures", () => {
    expect(
      significanceLabel({ ...twoGroup, n_significant_fdr_lfc: 1234,
                          n_genes_tested: 18551 }),
    ).toBe("1,234 of 18,551 significant");
  });
});

// --------------------------------------------------------------------------- //
// The panel itself, rendered
// --------------------------------------------------------------------------- //

const ROWS = [
  twoGroup,
  { ...twoGroup, contrast: "pHORST_vs_Col-0", group1: "pHORST",
    file_path: "de/d/Cortex__pHORST_vs_Col-0.json", n_significant_fdr_lfc: 99 },
  { ...neverRun, cluster_id: "Xylem" },
];

/** Resolves each download only when the test says so. */
const gate: { release: Record<string, () => void>; order: string[] } = {
  release: {}, order: [],
};

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => ({
      select: () => ({ eq: async () => ({ data: ROWS, error: null }) }),
    }),
    storage: {
      from: () => ({
        download: (path: string) =>
          new Promise((resolve) => {
            gate.order.push(path);
            gate.release[path] = () =>
              resolve({
                data: {
                  text: async () =>
                    JSON.stringify([
                      { gene: path, p_val: 0.01, avg_log2FC: 2,
                        "pct.1": 0.5, "pct.2": 0.1, p_val_adj: 0.01,
                        _row: path },
                    ]),
                },
                error: null,
              });
          }),
      }),
    },
  }),
}));

describe("the panel", () => {
  // Shared across tests, so a later one could otherwise satisfy its own wait
  // against the previous test's residue and race its own render.
  beforeEach(() => {
    gate.order.length = 0;
    gate.release = {};
  });

  const first = "de/d/Cortex__pFACT_vs_Col-0.json";
  const second = "de/d/Cortex__pHORST_vs_Col-0.json";

  it("ignores an answer that lands after the comparison changed", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(first));

    fireEvent.mouseDown(screen.getByLabelText("Comparison"));
    fireEvent.click(await screen.findByRole("option", { name: /pHORST_vs_Col-0/ }));
    await waitFor(() => expect(gate.order).toContain(second));

    // The one now on screen answers first, then the abandoned one answers.
    gate.release[second]();
    await screen.findAllByText(second);

    await act(async () => {
      gate.release[first]();
      // Let the abandoned answer run all the way through parsing, so this
      // fails if nothing stops it writing itself onto the screen.
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });

    expect(screen.queryByText(first)).toBeNull();
    expect(screen.queryAllByText(second).length).toBeGreaterThan(0);
  });

  it("names both groups and their sizes where the reader can see them", async () => {
    // The panel's central claim. Every string function below was tested; none
    // of it was ever asserted as rendered, so deleting this sentence, swapping
    // the two names or swapping the two counts all left the suite green.
    render(<Panel file_id={1} />);
    gate.release[first]?.();

    expect(
      await screen.findByText(
        /pFACT \(245 cells\) against Col-0 \(172 cells\), in Cortex/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/A positive fold change is higher in pFACT than in Col-0/),
    ).toBeTruthy();
  });

  it("says a comparison was never run, with the sizes that explain why", async () => {
    render(<Panel file_id={1} />);
    await waitFor(() => expect(gate.order).toContain(first));

    fireEvent.mouseDown(screen.getByLabelText("Cell type"));
    fireEvent.click(await screen.findByRole("option", { name: "Xylem" }));

    expect(await screen.findByText(/This comparison was not run/)).toBeTruthy();
    expect(screen.getByText(/too few on one side to compare/)).toBeTruthy();
    expect(screen.getByText(/3 cells in pFACT/)).toBeTruthy();
  });
});

describe("the gene table's columns", () => {
  const headerOf = (entry: Parameters<typeof columnsFor>[0], field: string) =>
    columnsFor(entry).find(c => c.field === field)?.headerName;

  it("heads each per-group column with the group it belongs to", () => {
    // pct.1 is group1's cells and pct.2 is group2's. Heading them "% in
    // Cluster" and "% in Others" attributes each number to the wrong set.
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
    { p_val_adj: 0.01, avg_log2FC: 2.0 },   // clearly up
    { p_val_adj: 0.01, avg_log2FC: 0.7 },   // up at 0.5, not at 1.0
    { p_val_adj: 0.01, avg_log2FC: -0.7 },  // down at 0.5, not at 1.0
    { p_val_adj: 0.01, avg_log2FC: -3.0 },  // clearly down
    { p_val_adj: 0.20, avg_log2FC: 5.0 },   // large but not significant
  ];

  it("tells the two directions apart", () => {
    // Every other fixture here is symmetric, so swapping `up` and `down`
    // inside the counter left all of them green.
    expect(
      countSignificant(
        [
          { p_val_adj: 0.001, avg_log2FC: 2 },
          { p_val_adj: 0.001, avg_log2FC: 1.5 },
          { p_val_adj: 0.001, avg_log2FC: -2 },
        ] as never,
        0.05,
        0.5,
      ),
    ).toEqual({ up: 2, down: 1, total: 3 });
  });

  it("counts at the cuts the analysis used, which is what the stored counts mean", () => {
    expect(countSignificant(rows, 0.05, 0.5)).toEqual({ up: 2, down: 2, total: 4 });
  });

  it("a stricter fold-change cut drops the genes between the two", () => {
    // The panel used to hardcode 1.0 while the analysis used 0.5, so the plot
    // and the selector disagreed on 7 of this dataset's 13 comparisons.
    expect(countSignificant(rows, 0.05, 1.0)).toEqual({ up: 1, down: 1, total: 2 });
  });

  it("ignores fold change when the adjusted p-value does not pass", () => {
    expect(countSignificant(rows, 0.001, 0.5)).toEqual({ up: 0, down: 0, total: 0 });
  });

  it("treats the cuts as strict, so a gene exactly on one does not pass", () => {
    expect(countSignificant([{ p_val_adj: 0.05, avg_log2FC: 2 }], 0.05, 0.5).total)
      .toBe(0);
    expect(countSignificant([{ p_val_adj: 0.01, avg_log2FC: 0.5 }], 0.05, 0.5).total)
      .toBe(0);
  });

  it("counts nothing for an empty comparison", () => {
    expect(countSignificant([], 0.05, 0.5)).toEqual({ up: 0, down: 0, total: 0 });
  });
});
