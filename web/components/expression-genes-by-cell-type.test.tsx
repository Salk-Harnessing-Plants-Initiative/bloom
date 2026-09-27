// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Two cell types; genotype and transgene status recorded on some cells.
const CELLS = [
  { x: 0, y: 0, cluster_ordinal: 0, replicate: "a", genotype: "Col-0", facets: { transgene_pos: "False" } },
  { x: 0, y: 0, cluster_ordinal: 0, replicate: "a", genotype: "pFACT", facets: { transgene_pos: "True" } },
  { x: 0, y: 0, cluster_ordinal: 1, replicate: "a", genotype: "pFACT", facets: null },
  { x: 0, y: 0, cluster_ordinal: 1, replicate: "a", genotype: "Col-0", facets: null },
];
const CLUSTERS = [
  { ordinal: 0, cluster_id: "C", name: "Cortex", color: "#ff0000" },
  { ordinal: 1, cluster_id: "X", name: "Xylem", color: "#0000ff" },
];

vi.mock("@/components/expression-lib/gene-values", () => ({
  createGeneReader: () => ({
    cells: async () => CELLS,
    gene: async (gene: string) =>
      gene === "gX"
        ? { gene, missing: true }
        : { gene, values: Float32Array.from(gene === "g1" ? [0, 3, 1, 2] : [1, 0, 0, 5]) },
  }),
}));

vi.mock("@/components/expression-lib/scrna-client", () => ({
  fetchClusters: vi.fn(async () => CLUSTERS),
  fetchDataset: vi.fn(async () => ({ expression_units: "log1p normalised counts" })),
}));

vi.mock("@/components/expression-lib/starting-genes", () => ({
  loadStartingGenes: vi.fn(async () => ({ genes: ["g1", "g2", "gX"], source: "markers" })),
}));

vi.mock("@/components/expression-gene-search", () => ({
  ExpressionGeneSearch: () => null,
}));

import { ExpressionGenesByCellType } from "./expression-genes-by-cell-type";
import { loadStartingGenes } from "@/components/expression-lib/starting-genes";

afterEach(cleanup);

async function open() {
  render(<ExpressionGenesByCellType datasetId={1} />);
  await waitFor(() => expect(screen.getAllByTestId("dotplot-row")).toHaveLength(2));
}

describe("the Genes by cell type tab", () => {
  it("opens on the starting genes and names one with no stored expression", async () => {
    await open();
    expect(
      screen.getByText(
        "By default, each cell type's top marker genes. Add or remove genes as you like.",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/No stored expression in this dataset for: gX/)).toBeTruthy();
    expect(screen.getAllByTestId("dotplot-column")).toHaveLength(2);
  });

  it("says how many genes it took from the DE results, when it opens on those", async () => {
    vi.mocked(loadStartingGenes).mockResolvedValueOnce({ genes: ["g1", "g2"], source: "de" });
    await open();
    expect(
      screen.getByText(
        "By default, 2 genes from the differential expression results (FDR < 0.05, |log2FC| > 0.5). Add or remove genes as you like.",
      ),
    ).toBeTruthy();
  });

  it("offers the splits the cells record, and a column for each group when split", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "By genotype" }));
    await waitFor(() => expect(screen.getAllByTestId("dotplot-column")).toHaveLength(4));
    fireEvent.click(screen.getByRole("button", { name: "By transgene" }));
    // Cortex: Transgene+ and Transgene−; Xylem: Not recorded.
    await waitFor(() => expect(screen.getAllByTestId("dotplot-column")).toHaveLength(3));
  });

  it("shows the first gene by cell type, and another when it is chosen", async () => {
    await open();
    expect(screen.getByRole("heading", { name: "g1 expression by cell type" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show g2 by cell type" }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "g2 expression by cell type" })).toBeTruthy(),
    );
  });

  it("names the split in the heading", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "By genotype" }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "g1 expression by cell type and genotype" }),
      ).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "By transgene" }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "g1 expression by cell type and transgene status" }),
      ).toBeTruthy(),
    );
  });

  it("downloads the numbers as CSV", async () => {
    const createObjectURL = vi.fn(() => "blob:table");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0];
    expect(blob.type).toBe("text/csv");
  });
});
