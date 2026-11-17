// @vitest-environment jsdom
/** A link's gene (`?gene=`) colours the map when the UMAP tab opens. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

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
vi.mock("@/components/expression-gene-search", () => ({ ExpressionGeneSearch: () => null }));
vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => ({ select: () => ({ eq: async () => ({ data: [], error: null }) }) }),
  }),
}));
// The page's gene search: prefix matches, ignoring case.
vi.mock("@/components/expression-lib/scrna-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/expression-lib/scrna-client")>()),
  searchGenes: vi.fn(async (_dataset: number, q: string) =>
    ["AT1G01010", "AT1G01010.2"].filter((g) => g.toLowerCase().startsWith(q.toLowerCase())),
  ),
}));

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
});
