/**
 * Hiding a sample must remove its cells from the picture and change nothing
 * else. The map is normalised to fit whatever it is given, so filtering the
 * cells before packing them would rescale and recentre the plot — every
 * remaining cell would jump. These pin that hiding goes through the visibility
 * attribute instead, and show what filtering would have done.
 */

import { describe, expect, it } from "vitest";

import { packPositions, packVisibility } from "./expression-umap";
import type { CellArraysRow } from "./expression-lib/scrna-client";

function cell(x: number, y: number, ordinal: number, replicate: string | null):
  CellArraysRow {
  return { x, y, cluster_ordinal: ordinal, replicate };
}

const CELLS = [
  cell(0, 0, 0, "Col-0"),
  cell(10, 10, 1, "pFACT"),
  cell(20, 20, 0, "pHORST"),
  cell(100, 100, 1, "pFACT"),
];
const ORDINALS = new Uint8Array([0, 1, 0, 1]);

const none = new Set<never>();

describe("packVisibility", () => {
  it("shows every cell when nothing is hidden", () => {
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, none, none)),
    ).toEqual([1, 1, 1, 1]);
  });

  it("hides exactly the cells of the hidden sample", () => {
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, none, new Set(["pFACT"]))),
    ).toEqual([1, 0, 1, 0]);
  });

  it("keeps one entry per cell, so nothing is removed from the data", () => {
    const hidden = packVisibility(CELLS, ORDINALS, none, new Set(["pFACT"]));
    expect(hidden.length).toBe(CELLS.length);
  });

  it("hides a cell that either its cluster or its sample rules out", () => {
    expect(
      Array.from(
        packVisibility(CELLS, ORDINALS, new Set([0]), new Set(["pFACT"])),
      ),
    ).toEqual([0, 0, 0, 0]);
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, new Set([1]), none)),
    ).toEqual([1, 0, 1, 0]);
  });

  it("leaves cells with no sample showing whatever is hidden", () => {
    const unlabelled = [cell(0, 0, 0, null), cell(1, 1, 0, "Col-0")];
    expect(
      Array.from(
        packVisibility(unlabelled, new Uint8Array([0, 0]), none,
                       new Set(["Col-0", ""])),
      ),
    ).toEqual([1, 0]);
  });
});

describe("packPositions", () => {
  it("does not depend on what is hidden, because it never sees it", () => {
    const a = packPositions(CELLS);
    const b = packPositions(CELLS);
    expect(Array.from(a.positions)).toEqual(Array.from(b.positions));
    expect(a.normScale).toBe(b.normScale);
  });

  it("would move every remaining cell if the hidden ones were filtered out", () => {
    // Not how the component behaves — this is why it does not. Drop the far
    // cell and the whole plot rescales around what is left.
    const whole = packPositions(CELLS);
    const filtered = packPositions(CELLS.filter((c) => c.replicate !== "pFACT"));

    expect(filtered.normScale).not.toBe(whole.normScale);
    expect(filtered.normCenterX).not.toBe(whole.normCenterX);
    // The (20, 20) cell survives the filter — third of four before, second of
    // two after — and still lands somewhere else on the map.
    expect(filtered.positions[2]).not.toBe(whole.positions[4]);
  });

  it("keeps a cell's place when the set of cells is the same", () => {
    const before = packPositions(CELLS);
    const after = packPositions([...CELLS]);
    expect(Array.from(after.positions)).toEqual(Array.from(before.positions));
  });
});
