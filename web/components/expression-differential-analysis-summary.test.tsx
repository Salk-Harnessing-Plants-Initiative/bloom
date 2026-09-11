/**
 * How the all-cell-types summary sits in the differential expression tab: the
 * cuts above it govern both it and the comparison below, a result chosen in it
 * opens that comparison, and a failure in it leaves the comparison working.
 */

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import Panel, { columnsFor } from "./expression-differential-analysis";
import { FIRST_GROUP_COLOUR, SECOND_GROUP_COLOUR } from "./expression-lib/de-types";

afterEach(cleanup);

const comparison = (id: number, cluster: string, contrast: string, tested = true) => {
  const [group1, group2] = contrast.split("_vs_");
  return {
    id, cluster_id: cluster, contrast, group1, group2,
    n_group1: 120, n_group2: 80, n_genes_tested: tested ? 15000 : 0, tested,
  };
};

const COMPARISONS = [
  comparison(1, "Cortex", "pFACT_vs_Col-0"),
  comparison(2, "Cortex", "pHORST_vs_Col-0"),
  comparison(3, "Pericycle", "pFACT_vs_Col-0"),
  comparison(4, "Xylem", "pFACT_vs_Col-0", false),
];

const RUN = { id: 5, method: "scanpy-wilcoxon", completed_at: "2026-09-11T00:00:00Z", params: {} };

const stand: {
  summaryRows: Record<string, unknown>[];
  summaryError: string | null;
  summaryCuts: number[];
  geneReads: number[];
} = { summaryRows: [], summaryError: null, summaryCuts: [], geneReads: [] };

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: (table: string) => {
      const filters: Record<string, unknown> = {};
      let start = 0;
      const query = {
        select: () => query,
        eq: (column: string, value: unknown) => { filters[column] = value; return query; },
        in: () => { filters.summary = true; return query; },
        lt: (_column: string, value: unknown) => { filters.fdr = value; return query; },
        or: () => query,
        order: () => query,
        limit: () => query,
        range: (from: number) => { start = from; return query; },
        then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) => {
          const answer = (() => {
            if (table === "scrna_de_runs") return { data: [RUN], error: null };
            if (table === "scrna_de") return { data: COMPARISONS, error: null };
            if (filters.summary) {
              if (start === 0) stand.summaryCuts.push(filters.fdr as number);
              if (stand.summaryError) return { data: null, error: { message: stand.summaryError } };
              return { data: start === 0 ? stand.summaryRows : [], error: null };
            }
            if (start > 0) return { data: [], error: null };
            const deId = filters.de_id as number;
            stand.geneReads.push(deId);
            return {
              data: [{ log2fc: 2, pvalue: 0.001, fdr: 0.01, pct_1: 0.5, pct_2: 0.1,
                       scrna_genes: { gene_name: `gene-of-${deId}` } }],
              error: null,
            };
          })();
          return Promise.resolve(answer).then(resolve, reject);
        },
      };
      return query;
    },
  }),
}));

const summaryRow = (deId: number, geneId: number, gene: string, log2fc: number, fdr: number) => ({
  de_id: deId, gene_id: geneId, log2fc, pvalue: fdr / 10, fdr,
  pct_1: 0.4, pct_2: 0.1, scrna_genes: { gene_name: gene },
});

const scrolled = vi.fn();

beforeEach(() => {
  stand.summaryRows = [summaryRow(3, 10, "AT1G01010", 1.4, 1e-5), summaryRow(1, 10, "AT1G01010", 1.1, 1e-3)];
  stand.summaryError = null;
  stand.summaryCuts = [];
  stand.geneReads = [];
  scrolled.mockClear();
  // jsdom lays nothing out, so it has no scrollIntoView of its own.
  Element.prototype.scrollIntoView = scrolled;
});

const summaryHeader = () => screen.findByRole("button", { name: /All cell type summary/ });

describe("the summary in the tab", () => {
  it("sits between the cuts and the comparison it opens", async () => {
    render(<Panel file_id={1} />);
    const header = await summaryHeader();
    const cuts = screen.getByLabelText("FDR below");
    const cellType = screen.getByLabelText("Cell type");
    expect(cuts.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(header.compareDocumentPosition(cellType) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the cuts even when the comparison selected has no genes to show", async () => {
    render(<Panel file_id={1} />);
    fireEvent.mouseDown(await screen.findByLabelText("Cell type"));
    fireEvent.click(await screen.findByRole("option", { name: "Xylem" }));
    expect(await screen.findByText(/This comparison was not run/)).toBeTruthy();
    expect(screen.getByLabelText("FDR below")).toBeTruthy();
  });

  it("reads the summary again at the new cut when a cut changes", async () => {
    render(<Panel file_id={1} />);
    await screen.findByText(/1 gene passes in 2 of 3 tested comparisons \(FDR < 0\.05/);
    fireEvent.change(screen.getByLabelText("FDR below"), { target: { value: "0.1" } });
    await waitFor(() => expect(stand.summaryCuts).toEqual([0.05, 0.1]));
    expect(
      await screen.findByText(/1 gene passes in 2 of 3 tested comparisons \(FDR < 0\.1, \|log2FC\| > 0\.5\)/),
    ).toBeTruthy();
  });

  it("opens a result's comparison below, and stays open on the same tab", async () => {
    render(<Panel file_id={1} />);
    await screen.findByText(/1 gene passes/);
    fireEvent.click(await summaryHeader());
    fireEvent.click(await screen.findByRole("tab", { name: "All results" }));
    const pericycle = within(screen.getByRole("table")).getByText("Pericycle");
    fireEvent.click(pericycle);

    await waitFor(() => expect(stand.geneReads).toContain(3));
    expect(await screen.findAllByText("gene-of-3")).not.toHaveLength(0);
    expect(within(screen.getByLabelText("Cell type")).getByText("Pericycle")).toBeTruthy();
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect((await summaryHeader()).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("tab", { name: "All results" }).getAttribute("aria-selected")).toBe("true");
  });

  it("leaves the comparison working when the summary cannot be read", async () => {
    stand.summaryError = "statement timeout";
    render(<Panel file_id={1} />);
    expect(await screen.findByText(/The summary could not be loaded/)).toBeTruthy();
    expect(await screen.findAllByText("gene-of-1")).not.toHaveLength(0);
  });

  it("names each direction by its group in the comparison's counts", async () => {
    render(<Panel file_id={1} />);
    expect(await screen.findByText("1 higher in pFACT")).toBeTruthy();
    expect(screen.getByText("0 higher in Col-0")).toBeTruthy();
  });
});

describe("the comparison table's fold changes", () => {
  const cell = (value: number) => {
    const column = columnsFor(COMPARISONS[0]).find((c) => c.field === "avg_log2FC");
    return column?.renderCell?.({ value } as never) as { props: { style: { color: string }; children: string } };
  };

  it("are red when higher in the first group and blue when higher in the second", () => {
    expect(cell(2).props.style.color).toBe(FIRST_GROUP_COLOUR);
    expect(cell(-2).props.style.color).toBe(SECOND_GROUP_COLOUR);
  });

  it("print an infinite fold change as a signed infinity", () => {
    expect(cell(Infinity).props.children).toBe("+∞");
  });
});
