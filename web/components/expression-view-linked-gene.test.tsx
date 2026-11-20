// @vitest-environment jsdom
/** A link's gene (`?gene=`) colours the map when the UMAP tab opens. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ExpressionUmapProps } from "./expression-umap";

/** Every set of props the map has been rendered with, newest last. */
const umapProps: ExpressionUmapProps[] = [];

vi.mock("@/components/expression-umap", () => ({
  ExpressionUmap: (props: ExpressionUmapProps) => {
    umapProps.push(props);
    return <div data-testid="umap-stub" />;
  },
}));
vi.mock("@/components/expression-cluster-detail-panel", () => ({
  ExpressionClusterDetailPanel: () => null,
}));
// The search box reads genes from the site; stand in for picking one.
vi.mock("@/components/expression-gene-search", () => ({
  ExpressionGeneSearch: ({ onChange }: { onChange: (gene: string | null) => void }) => (
    <button type="button" onClick={() => onChange("AT9G99990")}>Pick AT9G99990</button>
  ),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => ({ select: () => ({ eq: async () => ({ data: [], error: null }) }) }),
  }),
}));
// The page's gene search: prefix matches, ignoring case; dataset 2 spells its genes in lower case.
vi.mock("@/components/expression-lib/scrna-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/expression-lib/scrna-client")>()),
  searchGenes: vi.fn(async (dataset: number, q: string) =>
    (dataset === 2 ? ["at1g01010"] : ["AT1G01010", "AT1G01010.2"]).filter((g) =>
      g.toLowerCase().startsWith(q.toLowerCase()),
    ),
  ),
}));

import { searchGenes } from "@/components/expression-lib/scrna-client";

/** Holds the next gene search until the test releases it. */
function holdNextSearch() {
  let release: (genes: string[]) => void = () => {};
  vi.mocked(searchGenes).mockImplementationOnce(
    () => new Promise<string[]>((resolve) => (release = resolve)),
  );
  return (genes: string[]) => release(genes);
}

const latest = () => umapProps[umapProps.length - 1];

beforeEach(() => {
  umapProps.length = 0;
});
afterEach(cleanup);

describe("ExpressionView — a gene from the link", () => {
  it("colours the map by the link's gene, as the dataset spells it", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} linkedGene="at1g01010" />);
    await waitFor(() => expect(latest().geneName).toBe("AT1G01010"));
  });

  it("says a gene the dataset doesn't have is not in it, and keeps the cell types", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} linkedGene="nope" />);
    await waitFor(() => expect(screen.getByText("nope is not in this dataset.")).toBeTruthy());
    expect(latest().geneName ?? null).toBeNull();
  });

  it("colours by cell type without a link", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    await waitFor(() => expect(umapProps.length).toBeGreaterThan(0));
    expect(latest().geneName ?? null).toBeNull();
  });

  it("keeps a gene the user picked before the link's gene was found", async () => {
    const release = holdNextSearch();
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} linkedGene="AT1G01010" />);
    fireEvent.click(screen.getByRole("button", { name: "Pick AT9G99990" }));
    release(["AT1G01010"]);
    await new Promise((r) => setTimeout(r, 20));
    expect(latest().geneName).toBe("AT9G99990");
  });

  it("uses only the dataset shown when the dataset changes mid-lookup", async () => {
    const releaseFirst = holdNextSearch();
    const releaseSecond = holdNextSearch();
    const { ExpressionView } = await import("./expression-view");
    const { rerender } = render(<ExpressionView datasetId={1} linkedGene="AT1G01010" />);
    rerender(<ExpressionView datasetId={2} linkedGene="AT1G01010" />);
    // The first dataset's answer arrives late, after the switch.
    releaseFirst(["AT1G01010"]);
    await new Promise((r) => setTimeout(r, 20));
    releaseSecond(["at1g01010"]);
    await waitFor(() => expect(latest().geneName).toBe("at1g01010"));
  });

  it("says the link's gene could not be looked up when its search fails", async () => {
    vi.mocked(searchGenes).mockRejectedValueOnce(new Error("gateway timeout"));
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} linkedGene="AT1G01010" />);
    await waitFor(() => expect(screen.getByText("Could not look up AT1G01010.")).toBeTruthy());
    expect(screen.queryByText(/is not in this dataset/)).toBeNull();
  });

  it("drops the not-found note when the link no longer names a gene", async () => {
    const { ExpressionView } = await import("./expression-view");
    const { rerender } = render(<ExpressionView datasetId={1} linkedGene="nope" />);
    await waitFor(() => expect(screen.getByText("nope is not in this dataset.")).toBeTruthy());
    rerender(<ExpressionView datasetId={1} />);
    await waitFor(() => expect(screen.queryByText("nope is not in this dataset.")).toBeNull());
  });
});
