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
 * record what they were given and the draw commands what they were asked to
 * draw, which is what these assert on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";

import type { CellArraysRow } from "./expression-lib/scrna-client";
import { EXPRESSION_VERT, VALUE_PASS } from "./expression-lib/shaders";

interface StubBuffer {
  initial: unknown;
  subdata: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
}

/** Every buffer regl was asked for, in creation order: position, color,
 *  visibility, expression, focus -- see the init effect. */
const buffers: StubBuffer[] = [];

/** Every draw command regl built, with the vertex shader it was built from. */
const draws: { vert: string; draw: ReturnType<typeof vi.fn> }[] = [];

vi.mock("regl", () => {
  const makeRegl = () => {
    const regl = vi.fn((config: { vert: string }) => {
      const draw = vi.fn();
      draws.push({ vert: config.vert, draw });
      return draw;
    }) as unknown as Record<string, unknown> & (() => unknown);
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

/** Each gene's values, released only when a test says so. */
const genes = vi.hoisted(() => ({
  release: new Map<string, (values: Float32Array) => void>(),
}));

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
    fetchGeneCounts: vi.fn(
      (_datasetId: number, gene: string) =>
        new Promise<Float32Array>((resolve) => genes.release.set(gene, resolve)),
    ),
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

/** Animation frames, run one at a time by the test rather than by the clock. */
let frames: FrameRequestCallback[] = [];
const runFrame = () => {
  const due = frames;
  frames = [];
  for (const frame of due) frame(0);
};

beforeEach(() => {
  buffers.length = 0;
  draws.length = 0;
  genes.release.clear();
  frames = [];
  packPositions.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", (frame: FrameRequestCallback) => {
    frames.push(frame);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The buffers in the order the init effect creates them. */
const positionBuffer = () => buffers[0];
const visibilityBuffer = () => buffers[2];
const expressionBuffer = () => buffers[3];

/** The expression draw command, and the passes it was asked for. */
const expressionDraw = () => draws.find((d) => d.vert === EXPRESSION_VERT)?.draw;
const passesDrawn = () =>
  (expressionDraw()?.mock.calls ?? []).map(([props]) => ({
    focusMode: props.focusMode,
    valuePass: props.valuePass,
  }));

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

describe("ExpressionUmap — focusing", () => {
  const focusBuffer = () => buffers[4];

  it("starts with every cell in focus", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    render(<ExpressionUmap datasetId={1} />);
    await waitFor(() => expect(buffers.length).toBeGreaterThan(4));
    expect(Array.from(focusBuffer().initial as Float32Array)).toEqual([1, 1, 1, 1]);
  });

  it("writes exactly the cells in focus, and moves and hides nothing", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    const { rerender } = render(<ExpressionUmap datasetId={1} />);
    await waitFor(() => expect(buffers.length).toBeGreaterThan(4));

    rerender(
      <ExpressionUmap datasetId={1} focusedValues={new Map([["sample", new Set(["pFACT"])]])} />,
    );
    await waitFor(() => expect(focusBuffer().subdata).toHaveBeenCalled());

    // cells 0 and 2 are pFACT; cell 3 records no sample, so it is greyed out
    const written = focusBuffer().subdata.mock.calls.at(-1)?.[0];
    expect(Array.from(written as Float32Array)).toEqual([1, 0, 1, 0]);
    expect(positionBuffer().subdata).not.toHaveBeenCalled();
    const visible = visibilityBuffer().subdata.mock.calls.at(-1)?.[0];
    if (visible) expect(Array.from(visible as Float32Array)).toEqual([1, 1, 1, 1]);
  });
});

describe("ExpressionUmap — changing gene", () => {
  const A = new Float32Array([0, 2, 0, 4]);
  const B = new Float32Array([1, 0, 0, 0]);
  const lastWritten = () =>
    Array.from((expressionBuffer().subdata.mock.calls.at(-1)?.[0] ?? []) as Float32Array);

  it("never shows the first gene's values or range under the second gene's name", async () => {
    const { ExpressionUmap } = await import("./expression-umap");
    const onRange = vi.fn();
    const { rerender } = render(
      <ExpressionUmap datasetId={1} geneName="A" onExpressionRangeChanged={onRange} />,
    );
    await waitFor(() => expect(genes.release.has("A")).toBe(true));
    act(() => genes.release.get("A")!(A));
    await waitFor(() => expect(lastWritten()).toEqual([0, 2, 0, 4]));
    expect(onRange).toHaveBeenLastCalledWith({ min: 0, max: 4 });

    rerender(<ExpressionUmap datasetId={1} geneName="B" onExpressionRangeChanged={onRange} />);
    await waitFor(() => expect(genes.release.has("B")).toBe(true));

    // B's values have not arrived: nothing of A may be left standing for B.
    await waitFor(() => expect(onRange).toHaveBeenLastCalledWith(null));
    expect(lastWritten()).toEqual([0, 0, 0, 0]);

    act(() => genes.release.get("B")!(B));
    await waitFor(() => expect(lastWritten()).toEqual([1, 0, 0, 0]));
    expect(onRange).toHaveBeenLastCalledWith({ min: 0, max: 1 });
  });
});

describe("ExpressionUmap — drawing order of a gene's cells", () => {
  async function showGene(focusedValues?: Map<string, Set<string>>) {
    const { ExpressionUmap } = await import("./expression-umap");
    render(<ExpressionUmap datasetId={1} geneName="A" focusedValues={focusedValues} />);
    await waitFor(() => expect(genes.release.has("A")).toBe(true));
    act(() => genes.release.get("A")!(new Float32Array([0, 2, 0, 4])));
    await waitFor(() => expect(expressionBuffer().subdata).toHaveBeenCalled());
    expressionDraw()?.mockClear();
    act(() => runFrame());
  }

  it("draws the cells at zero first and the expressing cells over them", async () => {
    await showGene();
    expect(passesDrawn()).toEqual([
      { focusMode: 0, valuePass: VALUE_PASS.ZERO },
      { focusMode: 0, valuePass: VALUE_PASS.POSITIVE },
    ]);
  });

  it("keeps the greyed-out cells in one pass, under both passes of the cells in focus", async () => {
    await showGene(new Map([["sample", new Set(["pFACT"])]]));
    expect(passesDrawn()).toEqual([
      { focusMode: 1, valuePass: VALUE_PASS.ALL },
      { focusMode: 2, valuePass: VALUE_PASS.ZERO },
      { focusMode: 2, valuePass: VALUE_PASS.POSITIVE },
    ]);
  });
});
