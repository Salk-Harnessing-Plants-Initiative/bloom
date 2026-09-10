// @vitest-environment jsdom
/**
 * The toggles and the map are wired together here, and nothing else tested it:
 * the control could render, light up and fire its handler while the map never
 * heard about it. Deleting `hiddenSamples={hiddenSamples}` from the
 * <ExpressionUmap> element left the whole suite green.
 *
 * So these drive the real ExpressionView and assert what the map is actually
 * handed, with the canvas stubbed out -- it needs a WebGL context that jsdom
 * does not have.
 */

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

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => ({
      select: () => ({ eq: async () => ({ data: [], error: null }) }),
    }),
  }),
}));

const DATASET = { id: 1, name: "ds", expression_units: null } as never;
const CLUSTERS = [
  { ordinal: 0, cluster_id: "Cortex", name: "Cortex", color: "#112233" },
] as never;

const LOADED = {
  dataset: DATASET,
  clusters: CLUSTERS,
  cellCount: 9,
  orphanCount: 0,
  samples: [
    { name: "Col-0", count: 4 },
    { name: "pFACT", count: 5 },
  ],
  unlabelledCount: 0,
};

/** The map reports its data once it has loaded; drive that from the stub. */
function loadData() {
  umapProps[umapProps.length - 1].onDataLoaded?.(LOADED as never);
}

const latest = () => umapProps[umapProps.length - 1];

beforeEach(() => {
  umapProps.length = 0;
});
afterEach(cleanup);

describe("ExpressionView — sample filtering reaches the map", () => {
  it("hands the map the sample the user switched off", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    expect([...(latest().hiddenSamples ?? [])]).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));

    await waitFor(() =>
      expect([...(latest().hiddenSamples ?? [])]).toEqual(["Col-0"]),
    );
  });

  it("hands back a sample switched on again, and only that one", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    fireEvent.click(screen.getByRole("button", { name: "pFACT 5" }));
    await waitFor(() =>
      expect([...(latest().hiddenSamples ?? [])].sort()).toEqual([
        "Col-0",
        "pFACT",
      ]),
    );

    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    await waitFor(() =>
      expect([...(latest().hiddenSamples ?? [])]).toEqual(["pFACT"]),
    );
  });

  it("clears every hidden sample when the way back is used", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    fireEvent.click(screen.getByRole("button", { name: "pFACT 5" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Show all samples" })).toBeTruthy(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Show all samples" }));
    await waitFor(() => expect([...(latest().hiddenSamples ?? [])]).toEqual([]));
  });

  it("says the figures around the map do not follow the filter", async () => {
    // Cluster sizes and the no-cluster count come from the whole dataset. With
    // one genotype soloed they describe something other than what is drawn, and
    // a reader has no way to tell.
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    expect(screen.queryByText(/for the whole dataset/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    await waitFor(() =>
      expect(screen.getByText(/for the whole dataset/)).toBeTruthy(),
    );
  });

  it("does not carry a hidden sample over to another dataset", async () => {
    const { ExpressionView } = await import("./expression-view");
    const { rerender } = render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    await waitFor(() =>
      expect([...(latest().hiddenSamples ?? [])]).toEqual(["Col-0"]),
    );

    // Col-0 is in most Arabidopsis datasets, so a carried-over hidden set would
    // open the next one with the wild type already switched off.
    rerender(<ExpressionView datasetId={2} />);
    await waitFor(() => expect([...(latest().hiddenSamples ?? [])]).toEqual([]));
  });
});
