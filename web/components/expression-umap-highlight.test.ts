/**
 * Which cells a highlight lights up. It reads the same values the filter rows
 * do, so a highlight on a label only some cells carry must leave the others
 * alone, and a cell matched by two rows is still one highlighted cell.
 */

import { describe, expect, it } from "vitest";

import { packHighlight } from "./expression-lib/umap-packing";

const CELLS = [
  { replicate: "Col-0", facets: { transgene_pos: "False" } },
  { replicate: "pFACT", facets: { transgene_pos: "True" } },
  { replicate: "pFACT", facets: null },
  { replicate: null, facets: { transgene_pos: "True" } },
];

describe("packHighlight", () => {
  it("marks the cells whose label value is highlighted, and no cell without the label", () => {
    const highlighted = new Map([["transgene_pos", new Set(["True"])]]);
    expect([...packHighlight(CELLS, highlighted)]).toEqual([0, 1, 0, 1]);
  });

  it("marks the cells of a highlighted sample", () => {
    const highlighted = new Map([["sample", new Set(["pFACT"])]]);
    expect([...packHighlight(CELLS, highlighted)]).toEqual([0, 1, 1, 0]);
  });

  it("marks a cell once when two rows both highlight it", () => {
    const highlighted = new Map([
      ["sample", new Set(["pFACT"])],
      ["transgene_pos", new Set(["True"])],
    ]);
    expect([...packHighlight(CELLS, highlighted)]).toEqual([0, 1, 1, 1]);
  });

  it("marks nothing when nothing is highlighted", () => {
    expect([...packHighlight(CELLS, new Map())]).toEqual([0, 0, 0, 0]);
    expect([...packHighlight(CELLS, new Map([["transgene_pos", new Set<string>()]]))])
      .toEqual([0, 0, 0, 0]);
  });
});
