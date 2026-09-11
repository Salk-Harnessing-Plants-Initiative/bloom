// @vitest-environment jsdom
/**
 * The toggles and the map are wired together here, and nothing else tested it:
 * the control could render, light up and fire its handler while the map never
 * heard about it. Deleting `hiddenValues={hiddenValues}` from the
 * <ExpressionUmap> element left the whole suite green.
 *
 * So these drive the real ExpressionView and assert what the map is actually
 * handed, with the canvas stubbed out -- it needs a WebGL context that jsdom
 * does not have.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ExpressionUmapProps } from "./expression-umap";

/** Exactly what the map reports, so a change to that contract breaks here. */
type LoadedPayload = Parameters<
  NonNullable<ExpressionUmapProps["onDataLoaded"]>
>[0];

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

const DATASET = { id: 1, name: "ds", expression_units: null } as unknown as
  LoadedPayload["dataset"];
const CLUSTERS = [
  { ordinal: 0, cluster_id: "Cortex", name: "Cortex", color: "#112233" },
] as unknown as LoadedPayload["clusters"];

const LOADED: LoadedPayload = {
  dataset: DATASET,
  clusters: CLUSTERS,
  cellCount: 9,
  orphanCount: 0,
  filters: ["sample"],
  unlabelled: { sample: 0 },
  cells: [
    ...Array(4).fill({ replicate: "Col-0", facets: null }),
    ...Array(5).fill({ replicate: "pFACT", facets: null }),
  ],
};

const DATASET_2: LoadedPayload = {
  ...LOADED,
  cellCount: 7,
  cells: Array(7).fill({ replicate: "WT", facets: null }),
};

/** The map reports its data once it has loaded; drive that from the stub. */
function loadData(payload: LoadedPayload = LOADED) {
  umapProps[umapProps.length - 1].onDataLoaded?.(payload);
}

const latest = () => umapProps[umapProps.length - 1];

/** The values of one filter row the map was last told to hide. */
const hiddenOf = (filter = "sample") => [...(latest().hiddenValues?.get(filter) ?? [])];

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
    expect(hiddenOf()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));

    await waitFor(() =>
      expect(hiddenOf()).toEqual(["Col-0"]),
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
      expect(hiddenOf().sort()).toEqual([
        "Col-0",
        "pFACT",
      ]),
    );

    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    await waitFor(() =>
      expect(hiddenOf()).toEqual(["pFACT"]),
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
    await waitFor(() => expect(hiddenOf()).toEqual([]));
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

  it("opens another dataset with nothing hidden", async () => {
    const { ExpressionView } = await import("./expression-view");
    const { rerender } = render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Col-0 4" }));
    await waitFor(() =>
      expect(hiddenOf()).toEqual(["Col-0"]),
    );

    // Col-0 is in most Arabidopsis datasets, so a carried-over hidden set would
    // open the next one with the wild type already switched off. Load the new
    // dataset rather than only switching the id: the reset firing is not the
    // same as the hidden set being clean once its cells arrive.
    rerender(<ExpressionView datasetId={2} />);
    loadData(DATASET_2);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "WT 7" })).toBeTruthy(),
    );
    expect(hiddenOf()).toEqual([]);
    expect(
      screen.getByRole("button", { name: "WT 7" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.queryByText(/for the whole dataset/)).toBeNull();
  });

  it("takes the previous dataset's chips down while the next one loads", async () => {
    // Left up, they stay clickable, and a click writes a name the new dataset
    // may not have into the hidden set -- which then cannot be cleared from the
    // UI, because no chip shows as hidden and the way-back button needs every
    // sample hidden to appear.
    const { ExpressionView } = await import("./expression-view");
    const { rerender } = render(<ExpressionView datasetId={1} />);
    loadData();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Col-0 4" })).toBeTruthy(),
    );

    rerender(<ExpressionView datasetId={2} />);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Col-0 4" })).toBeNull(),
    );
  });
});

describe("ExpressionView — label rows", () => {
  const LABELLED: LoadedPayload = {
    ...LOADED,
    cellCount: 5,
    filters: ["sample", "transgene_pos"],
    unlabelled: { sample: 0, transgene_pos: 0 },
    cells: [
      { replicate: "Col-0", facets: { transgene_pos: "False" } },
      { replicate: "Col-0", facets: { transgene_pos: "False" } },
      { replicate: "pFACT", facets: { transgene_pos: "True" } },
      { replicate: "pFACT", facets: { transgene_pos: "True" } },
      { replicate: "pFACT", facets: { transgene_pos: "False" } },
    ],
  };

  it("shows a row per label and hands the map the value switched off", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData(LABELLED);

    await waitFor(() => expect(screen.getByRole("button", { name: "True 2" })).toBeTruthy());
    expect(screen.getByText("transgene_pos")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "True 2" }));
    await waitFor(() => expect(hiddenOf("transgene_pos")).toEqual(["True"]));
    expect(hiddenOf()).toEqual([]);
  });

  it("recounts a label row against the samples shown", async () => {
    const { ExpressionView } = await import("./expression-view");
    render(<ExpressionView datasetId={1} />);
    loadData(LABELLED);

    await waitFor(() => expect(screen.getByRole("button", { name: "False 3" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Col-0 2" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "False 1" })).toBeTruthy());
    expect(screen.getByRole("button", { name: "True 2" })).toBeTruthy();
  });
});
