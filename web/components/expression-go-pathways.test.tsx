// @vitest-environment jsdom
/**
 * The GO & pathways tab reads the dataset's newest enrichment run and shows one comparison,
 * direction and kind at a time: a dot plot of the most significant terms and a table of all of
 * them. Pinned here: what it opens on, that switching direction or kind changes the terms, the
 * empty and error states, and that the footer says which annotation and genes were tested.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { EnrichmentFile, EnrichmentTerm } from "@/components/expression-lib/enrichment";

const fetched = vi.hoisted(() => ({ result: null as unknown, error: null as Error | null }));

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
vi.mock("@/components/expression-lib/enrichment", async (original) => ({
  ...(await original<typeof import("@/components/expression-lib/enrichment")>()),
  fetchLatestEnrichment: async () => {
    if (fetched.error) throw fetched.error;
    return fetched.result;
  },
}));

import { ExpressionGoPathways } from "./expression-go-pathways";

function term(over: Partial<EnrichmentTerm>): EnrichmentTerm {
  return {
    comparison: "2_vs_rest", direction: "up", kind: "GO_BP", term: "GO:0006412",
    name: "translation", list_genes_in_term: 30, list_size: 120, term_size: 300,
    background_size: 8000, p: 1e-12, fdr: 1e-10, genes: ["G1", "G2", "G3"], ...over,
  };
}

const FILE: EnrichmentFile = {
  format: "bloom-enrichment/1", dataset_id: 25, de_run_id: 5,
  annotation: "sbicolor.btx623.hpiv3.v1/eggnog-b1",
  params: { top_n: 300, fdr_max: 0.05, lfc_min: 0.25 },
  background_genes: 21957, annotated_genes: 11427,
  results: [
    term({}),
    term({ term: "GO:0042254", name: "ribosome biogenesis", fdr: 1e-5 }),
    term({ direction: "down", term: "GO:0009416", name: "response to light stimulus" }),
    term({ kind: "KEGG", term: "map03010", name: "Ribosome" }),
    term({ comparison: "10_vs_rest", term: "GO:0010015", name: "root morphogenesis" }),
  ],
};

beforeEach(() => {
  fetched.result = { run: { id: 9, de_run_id: 5, annotation_build_id: 3,
                            file_path: "enrichment/25/run-9.json.gz", ready_at: null },
                     file: FILE };
  fetched.error = null;
});
afterEach(cleanup);

const table = () => screen.getByRole("table", { name: /terms/i });

describe("ExpressionGoPathways", () => {
  it("opens on the first comparison's up-regulated biological processes", async () => {
    render(<ExpressionGoPathways datasetId={25} />);
    await waitFor(() => expect(table()).toBeTruthy());
    const rows = within(table()).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual([
      expect.stringContaining("translation"),
      expect.stringContaining("ribosome biogenesis"),
    ]);
    expect(screen.getByRole("img", { name: /dot plot/i })).toBeTruthy();
  });

  it("shows the down-regulated terms when asked", async () => {
    render(<ExpressionGoPathways datasetId={25} />);
    await waitFor(() => expect(table()).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Down" }));
    expect(within(table()).getByText(/response to light stimulus/)).toBeTruthy();
    expect(within(table()).queryByText(/translation/)).toBeNull();
  });

  it("shows KEGG pathways when asked", async () => {
    render(<ExpressionGoPathways datasetId={25} />);
    await waitFor(() => expect(table()).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "KEGG pathways" }));
    expect(within(table()).getByText("Ribosome")).toBeTruthy();
  });

  it("says when a choice has nothing significant", async () => {
    render(<ExpressionGoPathways datasetId={25} />);
    await waitFor(() => expect(table()).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Cellular component" }));
    expect(screen.getByText(/No significant cellular component terms/i)).toBeTruthy();
  });

  it("says what was tested", async () => {
    render(<ExpressionGoPathways datasetId={25} />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(screen.getByText(/sbicolor\.btx623\.hpiv3\.v1\/eggnog-b1/)).toBeTruthy();
    expect(screen.getByText(/11,427 of 21,957 genes/)).toBeTruthy();
  });

  it("says when the dataset has no GO and pathway results yet", async () => {
    fetched.result = null;
    render(<ExpressionGoPathways datasetId={25} />);
    expect(await screen.findByText(/No GO and pathway results for this dataset yet/i))
      .toBeTruthy();
  });

  it("says why when the file can't be read", async () => {
    fetched.error = new Error("the file is for dataset 26");
    render(<ExpressionGoPathways datasetId={25} />);
    expect(await screen.findByText(/the file is for dataset 26/)).toBeTruthy();
  });
});
