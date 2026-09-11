// @vitest-environment jsdom
/**
 * What the canvas hands the packing, and what it does with the result.
 *
 * The packing functions have their own tests, but those compare a function
 * against itself and cannot see the call site. Filtering the cells there --
 * `packPositions(cells.filter(...))` -- is the one regression this design
 * exists to prevent, and it passed every other test in the suite.
 *
 * regl needs a WebGL context jsdom does not have, so it is stubbed; the buffers
 * record what they were given, which is what these assert on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

import type { CellArraysRow } from "./expression-lib/scrna-client";

interface StubBuffer {
  initial: unknown;
  subdata: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
}

/** Every buffer regl was asked for, in creation order: position, color,
 *  visibility, expression -- see the init effect. */
const buffers: StubBuffer[] = [];

vi.mock("regl", () => {
  const makeRegl = () => {
    const regl = vi.fn(() => vi.fn()) as unknown as Record<string, unknown> &
      (() => unknown);
    Object.assign(regl, {
      buffer: vi.fn((initial: unknown) => {
        const b: StubBuffer = { initial, subdata: vi.fn(), destroy: vi.fn() };
        buffers.push(b);
        return b;
      }),
      prop: vi.fn((k: string) => k),
      poll: vi.fn(),
      clear: vi.fn(),
      destroy: vi.fn(),
    });
    return regl;
  };
  return { default: vi.fn(() => makeRegl()) };
});

// pFACT is seen first but sorts second, so first-seen order is distinguishable
// from alphabetical. One cell records no sample.
const CELLS: CellArraysRow[] = [
  { x: 0, y: 5, cluster_ordinal: 0, replicate: "pFACT" },
  { x: 10, y: 15, cluster_ordinal: 0, replicate: "Col-0" },
  { x: 20, y: 35, cluster_ordinal: 1, replicate: "pFACT" },
  { x: 30, y: 55, cluster_ordinal: 1, replicate: null },
];

vi.mock("./expression-lib/scrna-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("./expression-lib/scrna-client")
  >();
  return {
    ...actual,
    fetchDataset: vi.fn(async () => ({ id: 1, name: "ds" })),
    fetchClusters: vi.fn(async () => [
      { ordinal: 0, cluster_id: "A", name: "A", color: "#112233" },
      { ordinal: 1, cluster_id: "B", name: "B", color: "#445566" },
    ]),
    fetchCells: vi.fn(async () => CELLS),
    fetchGeneBin: vi.fn(async () => null),
  };
});

const packPositions = vi.hoisted(() => vi.fn());
vi.mock("./expression-lib/umap-packing", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("./expression-lib/umap-packing")
  >();
  packPositions.mockImplementation(actual.packPositions);
  return { ...actual, packPositions };
});

beforeEach(() => {
  buffers.length = 0;
  packPositions.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The buffers in the order the init effect creates them. */
const positionBuffer = () => buffers[0];
const visibilityBuffer = () => buffers[2];

describe("ExpressionUmap — what reaches the packing", () => {
  it("packs every cell, not the ones left showing", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    render(<ExpressionUmap datasetId={1} hiddenValues={new Map([["sample", new Set(["pFACT"])]])} />);

    await waitFor(() => expect(packPositions).toHaveBeenCalled());
    expect(packPositions.mock.calls[0][0]).toHaveLength(CELLS.length);
    expect(packPositions).toHaveBeenCalledTimes(1);
  });

  it("gives the canvas one position per cell even when a sample is hidden", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    render(<ExpressionUmap datasetId={1} hiddenValues={new Map([["sample", new Set(["pFACT"])]])} />);

    await waitFor(() => expect(buffers.length).toBeGreaterThan(2));
    expect((positionBuffer().initial as Float32Array).length).toBe(
      CELLS.length * 2,
    );
  });

  it("never rewrites the positions when the hidden set changes", async () => {
    // This is the whole design: the plot rescales to whatever it packs, so
    // re-packing on a filter would move every remaining cell.
    const { ExpressionUmap } = await import("./expression-umap");
    const { rerender } = render(
      <ExpressionUmap datasetId={1} hiddenValues={new Map()} />,
    );
    await waitFor(() => expect(buffers.length).toBeGreaterThan(2));
    const before = Array.from(positionBuffer().initial as Float32Array);

    rerender(<ExpressionUmap datasetId={1} hiddenValues={new Map([["sample", new Set(["Col-0"])]])} />);
    await waitFor(() => expect(visibilityBuffer().subdata).toHaveBeenCalled());

    expect(positionBuffer().subdata).not.toHaveBeenCalled();
    expect(Array.from(positionBuffer().initial as Float32Array)).toEqual(before);
    expect(packPositions).toHaveBeenCalledTimes(1);
  });

  it("switches off exactly the hidden sample's cells in the visibility buffer", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    const { rerender } = render(
      <ExpressionUmap datasetId={1} hiddenValues={new Map()} />,
    );
    await waitFor(() => expect(buffers.length).toBeGreaterThan(2));

    rerender(<ExpressionUmap datasetId={1} hiddenValues={new Map([["sample", new Set(["pFACT"])]])} />);
    await waitFor(() => expect(visibilityBuffer().subdata).toHaveBeenCalled());

    const written = visibilityBuffer().subdata.mock.calls.at(-1)?.[0];
    // cells 0 and 2 are pFACT; cell 3 records no sample and stays drawn
    expect(Array.from(written as Float32Array)).toEqual([0, 1, 0, 1]);
  });

  it("reports the filter rows and the cells, so the toggles can be built from them", async () => {
    // The view renders the toggles only when this payload carries rows, and
    // it mocks this component away to test itself. Without an assertion here,
    // `filters: []` in the report removes the whole feature from the page with
    // every test still passing -- and a page with no toggles is exactly what a
    // dataset recording no sample is meant to look like, so nothing would show.
    const { ExpressionUmap } = await import("./expression-umap");
    const onDataLoaded = vi.fn();
    render(<ExpressionUmap datasetId={1} onDataLoaded={onDataLoaded} />);

    await waitFor(() => expect(onDataLoaded).toHaveBeenCalled());
    const reported = onDataLoaded.mock.calls[0][0];
    expect(reported.filters).toEqual(["sample"]);
    expect(reported.unlabelled).toEqual({ sample: 1 });
    expect(reported.cells).toHaveLength(CELLS.length);
    expect(reported.cellCount).toBe(CELLS.length);
  });
});

describe("ExpressionUmap — highlighting", () => {
  const highlightBuffer = () => buffers[4];

  it("starts with no cell highlighted", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    render(<ExpressionUmap datasetId={1} />);
    await waitFor(() => expect(buffers.length).toBeGreaterThan(4));
    expect(Array.from(highlightBuffer().initial as Float32Array)).toEqual([0, 0, 0, 0]);
  });

  it("writes exactly the highlighted sample's cells, and moves and hides nothing", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    const { rerender } = render(<ExpressionUmap datasetId={1} />);
    await waitFor(() => expect(buffers.length).toBeGreaterThan(4));

    rerender(
      <ExpressionUmap datasetId={1} highlightedValues={new Map([["sample", new Set(["pFACT"])]])} />,
    );
    await waitFor(() => expect(highlightBuffer().subdata).toHaveBeenCalled());

    // cells 0 and 2 are pFACT; cell 3 records no sample and is not highlighted
    const written = highlightBuffer().subdata.mock.calls.at(-1)?.[0];
    expect(Array.from(written as Float32Array)).toEqual([1, 0, 1, 0]);
    expect(positionBuffer().subdata).not.toHaveBeenCalled();
    const visible = visibilityBuffer().subdata.mock.calls.at(-1)?.[0];
    if (visible) expect(Array.from(visible as Float32Array)).toEqual([1, 1, 1, 1]);
  });
});
