/**
 * Which cells a focus keeps in colour. Every chosen value has to hold at once
 * -- pFACT and transgene positive means cells that are both -- while two values
 * of one row mean either. A cell without the label cannot meet a focus on it.
 */

import { describe, expect, it } from "vitest";

import {
  countFocused,
  describeFocus,
  focusIsSet,
  packFocus,
} from "./expression-lib/umap-packing";

const CELLS = [
  { replicate: "Col-0", facets: { transgene_pos: "False" } },
  { replicate: "pFACT", facets: { transgene_pos: "True" } },
  { replicate: "pFACT", facets: null },
  { replicate: null, facets: { transgene_pos: "True" } },
  { replicate: "pFACT", facets: { transgene_pos: "False" } },
];

const focus = (rows: Record<string, string[]>) =>
  new Map(Object.entries(rows).map(([row, values]) => [row, new Set(values)]));

describe("packFocus", () => {
  it("keeps every cell in colour when nothing is chosen", () => {
    expect([...packFocus(CELLS, new Map())]).toEqual([1, 1, 1, 1, 1]);
    expect([...packFocus(CELLS, focus({ transgene_pos: [] }))]).toEqual([1, 1, 1, 1, 1]);
  });

  it("keeps only the cells with the chosen label value", () => {
    expect([...packFocus(CELLS, focus({ transgene_pos: ["True"] }))]).toEqual([0, 1, 0, 1, 0]);
  });

  it("keeps only cells meeting every chosen row at once", () => {
    const both = focus({ sample: ["pFACT"], transgene_pos: ["True"] });
    expect([...packFocus(CELLS, both)]).toEqual([0, 1, 0, 0, 0]);
  });

  it("reads two values of one row as either", () => {
    expect([...packFocus(CELLS, focus({ sample: ["Col-0", "pFACT"] }))]).toEqual([1, 1, 1, 0, 1]);
  });

  it("does not keep a cell that lacks the label being focused on", () => {
    // cell 2 has no transgene label, cell 3 has no sample
    expect(packFocus(CELLS, focus({ transgene_pos: ["True"] }))[2]).toBe(0);
    expect(packFocus(CELLS, focus({ sample: ["pFACT"] }))[3]).toBe(0);
  });
});

describe("countFocused", () => {
  it("counts the cells in focus that the filter rows leave on the map", () => {
    const both = focus({ sample: ["pFACT"], transgene_pos: ["True"] });
    expect(countFocused(CELLS, both, new Map())).toBe(1);
    expect(countFocused(CELLS, both, focus({ sample: ["pFACT"] }))).toBe(0);
    expect(countFocused(CELLS, focus({ transgene_pos: ["True"] }), focus({ sample: ["pFACT"] })))
      .toBe(1);
  });
});

describe("describeFocus", () => {
  it("says the focus in words, samples first", () => {
    expect(describeFocus(focus({ transgene_pos: ["True"], sample: ["pFACT"] })))
      .toBe("pFACT and transgene_pos True");
    expect(describeFocus(focus({ sample: ["Col-0", "pFACT"] }))).toBe("Col-0 or pFACT");
  });
});

describe("focusIsSet", () => {
  it("is set only when some row has a chosen value", () => {
    expect(focusIsSet(new Map())).toBe(false);
    expect(focusIsSet(focus({ sample: [] }))).toBe(false);
    expect(focusIsSet(focus({ sample: ["Col-0"] }))).toBe(true);
  });
});
