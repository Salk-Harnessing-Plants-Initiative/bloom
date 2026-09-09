/**
 * Hiding a sample must remove its cells from the picture and change nothing
 * else. The map is normalised to fit whatever it is given, so filtering the
 * cells before packing them would rescale and recentre the plot — every
 * remaining cell would jump. These pin that hiding goes through the visibility
 * attribute instead, and show what filtering would have done.
 */

import { describe, expect, it } from "vitest";

import { countsFor, packPositions, packVisibility } from "./expression-umap";
import type { CellArraysRow } from "./expression-lib/scrna-client";

function cell(x: number, y: number, ordinal: number, replicate: string | null,
              facets: Record<string, string> | null = null): CellArraysRow {
  return { x, y, cluster_ordinal: ordinal, replicate, facets };
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
      Array.from(packVisibility(CELLS, ORDINALS, none)),
    ).toEqual([1, 1, 1, 1]);
  });

  it("hides exactly the cells of the hidden sample", () => {
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, none, new Map([["sample", new Set(["pFACT"])]]))),
    ).toEqual([1, 0, 1, 0]);
  });

  it("keeps one entry per cell, so nothing is removed from the data", () => {
    const hidden = packVisibility(CELLS, ORDINALS, none, new Map([["sample", new Set(["pFACT"])]]));
    expect(hidden.length).toBe(CELLS.length);
  });

  it("hides a cell that either its cluster or its sample rules out", () => {
    expect(
      Array.from(
        packVisibility(CELLS, ORDINALS, new Set([0]), new Map([["sample", new Set(["pFACT"])]])),
      ),
    ).toEqual([0, 0, 0, 0]);
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, new Set([1]))),
    ).toEqual([1, 0, 1, 0]);
  });

  it("leaves cells with no sample showing whatever is hidden", () => {
    const unlabelled = [cell(0, 0, 0, null), cell(1, 1, 0, "Col-0")];
    expect(
      Array.from(
        packVisibility(unlabelled, new Uint8Array([0, 0]), none,
                       new Map([["sample", new Set(["Col-0", ""])]])),
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


describe("packVisibility with facets", () => {
  // Transgene status is the first facet: 232 of 8,683 cells on the real
  // dataset, and none of them in the control genotype.
  const FACETED = [
    cell(0, 0, 0, "Col-0", { transgene_pos: "False" }),
    cell(1, 1, 0, "pFACT", { transgene_pos: "True" }),
    cell(2, 2, 1, "pFACT", { transgene_pos: "False" }),
    cell(3, 3, 1, "pHORST", { transgene_pos: "True" }),
  ];
  const ORD = new Uint8Array([0, 0, 1, 1]);
  const nothing = new Set<never>();

  it("hides the cells carrying a hidden facet value", () => {
    const hidden = new Map([["transgene_pos", new Set(["False"])]]);
    expect(
      Array.from(packVisibility(FACETED, ORD, nothing, hidden)),
    ).toEqual([0, 1, 0, 1]);
  });

  it("combines with the sample toggles, so one genotype's positives can be shown alone", () => {
    const hidden = new Map([["transgene_pos", new Set(["False"])]]);
    const both = new Map([
      ["transgene_pos", new Set(["False"])],
      ["sample", new Set(["Col-0", "pHORST"])],
    ]);
    expect(
      Array.from(packVisibility(FACETED, ORD, nothing, both)),
    ).toEqual([0, 1, 0, 0]);
  });

  it("keeps one entry per cell, so nothing is removed from the data", () => {
    const hidden = new Map([["transgene_pos", new Set(["True", "False"])]]);
    expect(packVisibility(FACETED, ORD, nothing, hidden).length)
      .toBe(FACETED.length);
  });

  it("never hides a cell on a facet it has no value for", () => {
    const mixed = [cell(0, 0, 0, "a", { other: "x" }), cell(1, 1, 0, "a", null)];
    const hidden = new Map([["transgene_pos", new Set(["False", "True"])]]);
    expect(
      Array.from(
        packVisibility(mixed, new Uint8Array([0, 0]), nothing, hidden),
      ),
    ).toEqual([1, 1]);
  });

  it("applies every facet, not just the first", () => {
    const two = [
      cell(0, 0, 0, "a", { one: "keep", two: "keep" }),
      cell(1, 1, 0, "a", { one: "keep", two: "drop" }),
    ];
    const hidden = new Map([["two", new Set(["drop"])]]);
    expect(
      Array.from(
        packVisibility(two, new Uint8Array([0, 0]), nothing, hidden),
      ),
    ).toEqual([1, 0]);
  });
});

describe("countsFor", () => {
  // The shape of the real dataset: the control genotype carries none of the
  // construct, and the other two carry a little of it.
  const CELLS_3 = [
    cell(0, 0, 0, "Col-0", { transgene_pos: "False" }),
    cell(1, 1, 0, "Col-0", { transgene_pos: "False" }),
    cell(2, 2, 0, "pFACT", { transgene_pos: "True" }),
    cell(3, 3, 0, "pFACT", { transgene_pos: "False" }),
    cell(4, 4, 0, "pHORST", { transgene_pos: "True" }),
  ];
  const nothingHidden = new Map<string, Set<string>>();

  it("counts every cell when nothing is filtered", () => {
    expect(countsFor(CELLS_3, nothingHidden, "transgene_pos")).toEqual([
      { name: "False", count: 3 },
      { name: "True", count: 2 },
    ]);
  });

  it("counts only what the other filters leave visible", () => {
    // Showing the control genotype alone: it has no transgene-positive cells,
    // so offering "2 positive" would read as a finding rather than a filter.
    const onlyCol0 = new Map([["sample", new Set(["pFACT", "pHORST"])]]);
    expect(countsFor(CELLS_3, onlyCol0, "transgene_pos")).toEqual([
      { name: "False", count: 2 },
      { name: "True", count: 0 },
    ]);
  });

  it("ignores its own hidden values, so a row does not zero itself out", () => {
    const hidden = new Map([["transgene_pos", new Set(["True"])]]);
    expect(countsFor(CELLS_3, hidden, "transgene_pos")).toEqual([
      { name: "False", count: 3 },
      { name: "True", count: 2 },
    ]);
  });

  it("keeps a value visible with a count of zero rather than dropping it", () => {
    const onlyCol0 = new Map([["sample", new Set(["pFACT", "pHORST"])]]);
    const names = countsFor(CELLS_3, onlyCol0, "transgene_pos").map((v) => v.name);
    expect(names).toEqual(["False", "True"]);
  });

  it("counts samples the same way, against the other filters", () => {
    const onlyPositive = new Map([["transgene_pos", new Set(["False"])]]);
    expect(countsFor(CELLS_3, onlyPositive, "sample")).toEqual([
      { name: "Col-0", count: 0 },
      { name: "pFACT", count: 1 },
      { name: "pHORST", count: 1 },
    ]);
  });

  it("skips cells that have no value for the filter", () => {
    const mixed = [...CELLS_3, cell(9, 9, 0, null, null)];
    expect(countsFor(mixed, nothingHidden, "sample").length).toBe(3);
  });
});
