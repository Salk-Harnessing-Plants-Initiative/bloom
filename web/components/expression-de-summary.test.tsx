/**
 * The summary is read by someone deciding which genes to open, so what is
 * pinned here is what they see: the totals in the collapsed header, the three
 * views, the contrast picker, that choosing a result opens its comparison, and
 * that the summary says so rather than show a partial, stale or misleading
 * picture.
 */

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import DeSummary from "./expression-de-summary";
import type { DeEntry } from "./expression-lib/de-types";

afterEach(cleanup);

type Row = Record<string, unknown>;

/** What the stand-in database answers, keyed by the FDR cut asked for. */
const db: {
  rowsFor: (fdr: number) => Row[];
  error: string | null;
  wait: (fdr: number) => Promise<void>;
  reads: number[];
} = { rowsFor: () => [], error: null, wait: () => Promise.resolve(), reads: [] };

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => {
      let fdr = 0;
      let from = 0;
      const query = {
        select: () => query,
        in: () => query,
        or: () => query,
        order: () => query,
        lt: (_column: string, value: number) => {
          fdr = value;
          return query;
        },
        range: (start: number) => {
          from = start;
          return query;
        },
        then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) => {
          if (from === 0) db.reads.push(fdr);
          const page = db.rowsFor(fdr).slice(from, from + 1000);
          return db
            .wait(fdr)
            .then(() => (db.error
              ? { data: null, error: { message: db.error } }
              : { data: page, error: null }))
            .then(resolve, reject);
        },
      };
      return query;
    },
  }),
}));

const entry = (id: number, cluster: string | null, contrast: string, tested = true): DeEntry => {
  const [group1, group2] = contrast.split("_vs_");
  return {
    id, cluster_id: cluster, contrast, group1, group2,
    n_group1: 100, n_group2: 90, n_genes_tested: tested ? 15000 : 0, tested,
  };
};

const COMPARISONS: DeEntry[] = [
  entry(1, "Cortex", "pFACT_vs_Col-0"),
  entry(2, "Cortex", "pHORST_vs_Col-0"),
  entry(3, "Pericycle", "pFACT_vs_Col-0"),
  entry(4, "Pericycle", "pHORST_vs_Col-0"),
  entry(5, "Xylem", "pFACT_vs_Col-0"),
  entry(6, "Xylem", "pHORST_vs_Col-0", false),
  entry(7, "Phloem", "pFACT_vs_Col-0"),
];

const row = (deId: number, geneId: number, gene: string, log2fc: number, fdr: number): Row => ({
  de_id: deId, gene_id: geneId, log2fc, pvalue: fdr / 10, fdr,
  pct_1: 0.5, pct_2: 0.25, scrna_genes: { gene_name: gene },
});

/** AT1G01010 passes in Cortex and Pericycle, AT3G03030 in Cortex and Xylem,
 *  AT2G02020 in Cortex only. Phloem is tested and has nothing. */
const ROWS: Row[] = [
  row(2, 11, "AT2G02020", -2, 1e-8),
  row(1, 10, "AT1G01010", 1.5, 1e-6),
  row(3, 10, "AT1G01010", 1.2, 1e-4),
  row(4, 10, "AT1G01010", -0.9, 0.01),
  row(1, 12, "AT3G03030", 0.8, 0.02),
  row(5, 12, "AT3G03030", 0.7, 0.03),
];

const CUTS = { fdr: 0.05, log2fc: 0.5 };

beforeEach(() => {
  db.rowsFor = () => ROWS;
  db.error = null;
  db.wait = () => Promise.resolve();
  db.reads = [];
});

const header = () => screen.getByRole("button", { name: /All cell type summary/ });

const open = async () => {
  await screen.findByText(/genes? pass/);
  fireEvent.click(header());
  return screen.findByRole("tab", { name: "Genes" });
};

const pick = async (name: string) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Contrast/ }));
  fireEvent.click(await screen.findByRole("option", { name }));
};

const bodyRows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
const firstCells = () => bodyRows().map((r) => within(r).getAllByRole("cell")[0].textContent);

describe("the summary", () => {
  it("is collapsed on arrival, with the totals and the cuts in its header", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    expect(
      await screen.findByText(/3 genes pass in 5 of 6 tested comparisons \(FDR < 0\.05, \|log2FC\| > 0\.5\)/),
    ).toBeTruthy();
    expect(header().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("says so when nothing passes, and shows no tables when opened", async () => {
    db.rowsFor = () => [];
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    expect(await screen.findByText(/No gene passes these cuts in any cell type/)).toBeTruthy();
    fireEvent.click(header());
    await waitFor(() => expect(screen.getAllByText(/No gene passes these cuts/).length).toBe(2));
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("ranks genes by the cell types they pass in, then by their lowest FDR", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await open();
    expect(firstCells()).toEqual(["AT1G01010", "AT3G03030", "AT2G02020"]);
    expect(within(bodyRows()[0]).getByText("2 of 4")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "All results" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "By cell type" })).toBeTruthy();
  });

  it("says the FDR is per comparison and the tally is not a combined test", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await open();
    expect(screen.getByText(/FDR is adjusted within each comparison, not across them/)).toBeTruthy();
    expect(screen.getByText(/it is not a combined test/)).toBeTruthy();
  });

  it("narrows every view and the header to the contrast picked", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await open();
    await pick("pHORST vs Col-0");

    expect(
      await screen.findByText(/2 genes pass in 2 of 2 tested comparisons for pHORST vs Col-0/),
    ).toBeTruthy();
    expect(firstCells()).toEqual(["AT2G02020", "AT1G01010"]);
    // One contrast picked, so directions are counted by group.
    expect(screen.getByRole("columnheader", { name: "Higher in pHORST" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Higher in Col-0" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "By cell type" }));
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Cell type", "pHORST vs Col-0"]);
  });

  it("offers no picker when the analysis compares only one way", async () => {
    const oneWay = COMPARISONS.filter((c) => c.contrast === "pFACT_vs_Col-0");
    render(<DeSummary comparisons={oneWay} cuts={CUTS} onSelect={() => {}} />);
    await open();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Higher in pFACT" })).toBeTruthy();
  });

  it("opens the comparison of a result, of a place a gene passes, and of a grid cell", async () => {
    const onSelect = vi.fn();
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={onSelect} />);
    await open();

    fireEvent.click(screen.getByRole("button", { name: /Xylem · pFACT vs Col-0 · higher in pFACT/ }));
    expect(onSelect).toHaveBeenLastCalledWith(COMPARISONS[4]);

    fireEvent.click(screen.getByRole("tab", { name: "All results" }));
    const first = bodyRows()[0];
    expect(within(first).getByText("AT2G02020")).toBeTruthy();
    fireEvent.click(first);
    expect(onSelect).toHaveBeenLastCalledWith(COMPARISONS[1]);

    fireEvent.click(screen.getByRole("tab", { name: "By cell type" }));
    fireEvent.click(screen.getByRole("button", { name: /Pericycle, pHORST vs Col-0/ }));
    expect(onSelect).toHaveBeenLastCalledWith(COMPARISONS[3]);
    expect(header().getAttribute("aria-expanded")).toBe("true");
  });

  it("marks an untested comparison in the grid and does nothing when it is clicked", async () => {
    const onSelect = vi.fn();
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={onSelect} />);
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "By cell type" }));
    const [, , xylem, phloem] = bodyRows();
    const [, pfact, phorst] = within(xylem).getAllByRole("cell");
    expect(phorst.textContent).toBe("not tested");
    fireEvent.click(phorst);
    expect(onSelect).not.toHaveBeenCalled();
    expect(pfact.textContent).toBe("pFACT 1 · Col-0 0");
    const [, phloemPfact, phloemPhorst] = within(phloem).getAllByRole("cell");
    expect(phloemPfact.textContent).toBe("0");
    expect(phloemPhorst.textContent).toBe("");
  });

  it("colours direction only once a contrast is picked, when red and blue mean one group each", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "All results" }));
    // Pooled: pHORST is the first group in one contrast and would be the second in another.
    expect(screen.getByText("-2.00").style.color).toBe("");
    expect(screen.getByText("1.50").style.color).toBe("");

    await pick("pHORST vs Col-0");
    await screen.findByText(/for pHORST vs Col-0/);
    expect(screen.getByText("-2.00").style.color).toBe("rgb(21, 101, 192)");
    await pick("pFACT vs Col-0");
    await screen.findByText(/for pFACT vs Col-0/);
    expect(screen.getByText("1.50").style.color).toBe("rgb(198, 40, 40)");
  });

  it("shows each result's group sizes", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "All results" }));
    expect(within(bodyRows()[0]).getByText("100 vs 90")).toBeTruthy();
    expect(within(bodyRows()[0]).getByText("50.0% vs 25.0%")).toBeTruthy();
  });

  it("summarises nothing when more results pass than it can count", async () => {
    db.rowsFor = () =>
      Array.from({ length: 5001 }, (_, i) => row(1, i + 100, `G${i}`, 1, 0.001));
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    expect(await screen.findByText(/More than 5,000 results pass these cuts/)).toBeTruthy();
    fireEvent.click(header());
    expect(await screen.findByText(/would miscount/)).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("says the summary could not be loaded, with the reason", async () => {
    db.error = "permission denied";
    render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    expect(await screen.findByText(/The summary could not be loaded$/)).toBeTruthy();
    fireEvent.click(header());
    expect(await screen.findByText(/permission denied/)).toBeTruthy();
  });

  it("says when no comparison in the analysis was tested, and asks for nothing", async () => {
    const untested = [entry(6, "Xylem", "pHORST_vs_Col-0", false)];
    render(<DeSummary comparisons={untested} cuts={CUTS} onSelect={() => {}} />);
    expect(await screen.findByText(/No comparison .*was tested/)).toBeTruthy();
    expect(db.reads).toEqual([]);
  });

  it("asks for nothing while a cut is not a number", async () => {
    render(<DeSummary comparisons={COMPARISONS} cuts={{ fdr: NaN, log2fc: 0.5 }} onSelect={() => {}} />);
    expect(await screen.findByText(/Enter a number for both cuts/)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 400));
    expect(db.reads).toEqual([]);
  });

  it("is not shown when no comparison belongs to a cell type", () => {
    const { container } = render(
      <DeSummary comparisons={[entry(1, null, "a_vs_b")]} cuts={CUTS} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("reads again when the cuts change, and drops the answer for the earlier cuts", async () => {
    let release: () => void = () => {};
    db.wait = (fdr) => (fdr === 0.05 ? new Promise<void>((r) => { release = r; }) : Promise.resolve());
    db.rowsFor = (fdr) => (fdr === 0.05 ? ROWS : ROWS.slice(0, 1));

    const { rerender } = render(<DeSummary comparisons={COMPARISONS} cuts={CUTS} onSelect={() => {}} />);
    await waitFor(() => expect(db.reads).toEqual([0.05]));
    rerender(<DeSummary comparisons={COMPARISONS} cuts={{ fdr: 0.1, log2fc: 0.5 }} onSelect={() => {}} />);
    expect(await screen.findByText(/1 gene passes in 1 of 6 tested comparisons \(FDR < 0\.1,/)).toBeTruthy();

    await act(async () => {
      release();
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
    expect(screen.getByText(/1 gene passes in 1 of 6 tested comparisons/)).toBeTruthy();
    expect(screen.queryByText(/3 genes pass/)).toBeNull();
  });
});
