/**
 * Hiding a sample must remove its cells from the picture and change nothing
 * else. The map is normalised to fit whatever it is given, so filtering the
 * cells before packing them would rescale and recentre the plot — every
 * remaining cell would jump. These pin that hiding goes through the visibility
 * attribute instead, and show what filtering would have done.
 */

import { describe, expect, it } from "vitest";

import {
  countsFor,
  packCellArrays,
  packPositions,
  packVisibility,
  type HiddenValues,
} from "./expression-lib/umap-packing";
import type { CellArraysRow } from "./expression-lib/scrna-client";

function cell(x: number, y: number, ordinal: number, replicate: string | null,
              facets: Record<string, string> | null = null): CellArraysRow {
  return { x, y, cluster_ordinal: ordinal, replicate, facets };
}

// x and y differ on every cell, so packing one from the other is visible; and
// the samples are first seen in the reverse of alphabetical order, so a sort
// cannot pass for first-seen order.
const CELLS = [
  cell(0, 5, 0, "pHORST"),
  cell(10, 15, 1, "pFACT"),
  cell(20, 35, 0, "Col-0"),
  cell(100, 55, 1, "pFACT"),
];
const ORDINALS = new Uint8Array(CELLS.map((c) => c.cluster_ordinal));

const NO_CLUSTERS: ReadonlySet<number> = new Set();
const NO_SAMPLES: HiddenValues = new Map();
const hide = (...names: string[]): HiddenValues => new Map([["sample", new Set(names)]]);

describe("packVisibility", () => {
  it("shows every cell when nothing is hidden", () => {
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, NO_CLUSTERS, NO_SAMPLES)),
    ).toEqual([1, 1, 1, 1]);
  });

  it("hides exactly the cells of the hidden sample", () => {
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, NO_CLUSTERS, hide("pFACT"))),
    ).toEqual([1, 0, 1, 0]);
  });

  it("keeps one entry per cell, so nothing is removed from the data", () => {
    const hidden = packVisibility(CELLS, ORDINALS, NO_CLUSTERS, hide("pFACT"));
    expect(hidden.length).toBe(CELLS.length);
  });

  it("hides a cell that either its cluster or its sample rules out", () => {
    expect(
      Array.from(
        packVisibility(CELLS, ORDINALS, new Set([0]), hide("pFACT")),
      ),
    ).toEqual([0, 0, 0, 0]);
    expect(
      Array.from(packVisibility(CELLS, ORDINALS, new Set([1]), NO_SAMPLES)),
    ).toEqual([1, 0, 1, 0]);
  });

  it("leaves cells with no sample showing whatever is hidden", () => {
    const unlabelled = [cell(0, 0, 0, null), cell(1, 1, 0, "Col-0")];
    expect(
      Array.from(
        packVisibility(unlabelled, new Uint8Array([0, 0]), NO_CLUSTERS,
                       hide("Col-0", "")),
      ),
    ).toEqual([1, 0]);
  });
});

describe("packPositions", () => {
  it("normalises the cells to the padded bounding box", () => {
    // Without a value here, mutating the scale or reading x from y survives:
    // comparing the function against itself passes either way.
    const { positions, normScale, normCenterX, normCenterY } =
      packPositions(CELLS);
    expect(normCenterX).toBe(50);
    expect(normCenterY).toBe(30);
    // the wider of the two ranges sets the scale, so the plot stays square
    expect(normScale).toBeCloseTo(2 / (100 * 1.05), 10);
    expect(Array.from(positions).map((v) => Number(v.toFixed(4)))).toEqual([
      -0.9524, -0.4762, -0.7619, -0.2857, -0.5714, 0.0952, 0.9524, 0.4762,
    ]);
  });

  it("would move every remaining cell if the hidden ones were filtered out", () => {
    // Not how the component behaves — this is why it does not. Drop the far
    // cell and the whole plot rescales around what is left.
    const whole = packPositions(CELLS);
    const filtered = packPositions(CELLS.filter((c) => c.replicate !== "pFACT"));

    expect(filtered.normScale).not.toBe(whole.normScale);
    expect(filtered.normCenterX).not.toBe(whole.normCenterX);
    // The (20, 35) cell survives the filter — third of four before, second of
    // two after — and still lands somewhere else on the map.
    expect(filtered.positions[2]).not.toBe(whole.positions[4]);
  });

});

describe("packCellArrays", () => {
  it("counts each sample's own cells, in the order they first appear", () => {
    expect(packCellArrays(CELLS).samples).toEqual([
      { name: "pHORST", count: 1 },
      { name: "pFACT", count: 2 },
      { name: "Col-0", count: 1 },
    ]);
  });

  it("counts a cell recording no sample apart, rather than as one", () => {
    const cells = [
      cell(0, 0, 0, "Col-0"),
      cell(1, 1, 0, null),
      cell(2, 2, 0, ""),
    ];
    const { samples, unlabelledCount } = packCellArrays(cells);
    expect(samples).toEqual([{ name: "Col-0", count: 1 }]);
    expect(unlabelledCount).toBe(2);
  });

  it("counts a row that carries no sample field at all as unlabelled", () => {
    // A cell query without the column returns rows with `replicate` absent.
    // `!== null` lets undefined through and keys every cell under one nameless
    // entry; the map then offers a toggle labelled with a bare number.
    const cells = [{ x: 0, y: 0, cluster_ordinal: 0 }] as unknown as
      CellArraysRow[];
    const { samples, unlabelledCount } = packCellArrays(cells);
    expect(samples).toEqual([]);
    expect(unlabelledCount).toBe(1);
  });

  it("gives back one cluster ordinal per cell, in cell order", () => {
    expect(Array.from(packCellArrays(CELLS).clusterOrdinals)).toEqual([
      0, 1, 0, 1,
    ]);
  });

  it("counts the cells whose cluster is missing from the catalogue", () => {
    const cells = [
      cell(0, 0, 255, "Col-0"),
      cell(1, 1, 0, "Col-0"),
      cell(2, 2, 255, "pFACT"),
    ];
    expect(packCellArrays(cells).orphanCount).toBe(2);
  });

  it("reports nothing for a dataset with no cells", () => {
    expect(packCellArrays([])).toEqual({
      clusterOrdinals: new Uint8Array(0),
      orphanCount: 0,
      samples: [],
      unlabelledCount: 0,
      filters: [],
      unlabelled: {},
    });
  });
});

describe("packVisibility with labels", () => {
  // Transgene status is the first label: 232 of 8,683 cells on the real
  // dataset, and none of them in the control genotype.
  const LABELLED = [
    cell(0, 0, 0, "Col-0", { transgene_pos: "False" }),
    cell(1, 1, 0, "pFACT", { transgene_pos: "True" }),
    cell(2, 2, 1, "pFACT", { transgene_pos: "False" }),
    cell(3, 3, 1, "pHORST", { transgene_pos: "True" }),
  ];
  const ORD = new Uint8Array([0, 0, 1, 1]);
  const nothing = new Set<number>();

  it("hides the cells carrying a hidden label value", () => {
    const hidden = new Map([["transgene_pos", new Set(["False"])]]);
    expect(Array.from(packVisibility(LABELLED, ORD, nothing, hidden))).toEqual([0, 1, 0, 1]);
  });

  it("combines with the sample row, so one genotype's positives can be shown alone", () => {
    const both = new Map([
      ["transgene_pos", new Set(["False"])],
      ["sample", new Set(["Col-0", "pHORST"])],
    ]);
    expect(Array.from(packVisibility(LABELLED, ORD, nothing, both))).toEqual([0, 1, 0, 0]);
  });

  it("keeps one entry per cell, so nothing is removed from the data", () => {
    const hidden = new Map([["transgene_pos", new Set(["True", "False"])]]);
    expect(packVisibility(LABELLED, ORD, nothing, hidden).length).toBe(LABELLED.length);
  });

  it("never hides a cell on a label it has no value for", () => {
    const mixed = [cell(0, 0, 0, "a", { other: "x" }), cell(1, 1, 0, "a", null)];
    const hidden = new Map([["transgene_pos", new Set(["False", "True"])]]);
    expect(Array.from(packVisibility(mixed, new Uint8Array([0, 0]), nothing, hidden))).toEqual([1, 1]);
  });

  it("applies every label, not just the first", () => {
    const two = [
      cell(0, 0, 0, "a", { one: "keep", two: "keep" }),
      cell(1, 1, 0, "a", { one: "keep", two: "drop" }),
    ];
    const hidden = new Map([["two", new Set(["drop"])]]);
    expect(Array.from(packVisibility(two, new Uint8Array([0, 0]), nothing, hidden))).toEqual([1, 0]);
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
  const nothingHidden: HiddenValues = new Map();

  it("counts every cell when nothing is filtered, in first-seen order", () => {
    expect(countsFor(CELLS_3, nothingHidden, "transgene_pos")).toEqual([
      { name: "False", count: 3 },
      { name: "True", count: 2 },
    ]);
  });

  it("counts only what the other rows leave visible", () => {
    // The control genotype alone has no transgene-positive cells, so offering
    // "2 positive" would read as a finding rather than a filter.
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

  it("counts samples the same way, against the other rows", () => {
    const onlyPositive = new Map([["transgene_pos", new Set(["False"])]]);
    expect(countsFor(CELLS_3, onlyPositive, "sample")).toEqual([
      { name: "Col-0", count: 0 },
      { name: "pFACT", count: 1 },
      { name: "pHORST", count: 1 },
    ]);
  });

  it("skips cells that have no value for the row", () => {
    const mixed = [...CELLS_3, cell(9, 9, 0, null, null)];
    expect(countsFor(mixed, nothingHidden, "sample").length).toBe(3);
  });
});

describe("packCellArrays filter rows", () => {
  it("offers the sample row first, then each label in first-seen order", () => {
    const cells = [
      cell(0, 0, 0, "Col-0", { transgene_pos: "False" }),
      cell(1, 1, 0, "pFACT", { transgene_pos: "True", saturn_timezone: "Meristem" }),
    ];
    const { filters, unlabelled } = packCellArrays(cells);
    expect(filters).toEqual(["sample", "transgene_pos", "saturn_timezone"]);
    // The first cell has no timezone, so it stays on the map whatever that row hides.
    expect(unlabelled).toEqual({ sample: 0, transgene_pos: 0, saturn_timezone: 1 });
  });

  it("offers no rows for cells with no sample and no labels", () => {
    expect(packCellArrays([cell(0, 0, 0, null)]).filters).toEqual([]);
  });

  it("does not let a label named like the sample row shadow it", () => {
    const { filters } = packCellArrays([cell(0, 0, 0, "Col-0", { sample: "x" })]);
    expect(filters).toEqual(["sample"]);
  });
});
