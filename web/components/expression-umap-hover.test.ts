/**
 * Hovering a cell should name it. Two things have to be right for that: finding
 * the cell actually under the cursor, and describing it from the catalogue
 * rather than from the number stored on the cell.
 *
 * Getting the first wrong is the dangerous one — it names a neighbouring cell,
 * confidently, and nothing on screen says otherwise.
 */

import { describe, expect, it } from "vitest";

import { describeCell, isClick, pickCell } from "./expression-umap";
import type { CellArraysRow } from "./expression-lib/scrna-client";

/** The shader draws at (position + translate) * zoom, so at zoom 1 and no
 *  translate, clip 0,0 is the middle of the canvas. */
const VIEW = { zoom: 1, translate: [0, 0] as [number, number], width: 400, height: 200 };

// Three cells: canvas centre, and one to each side.
const POSITIONS = new Float32Array([
  0, 0,      // -> 200, 100
  -0.5, 0,   // -> 100, 100
  0.5, 0.5,  // -> 300, 50
]);

describe("pickCell", () => {
  it("finds the cell under the cursor", () => {
    expect(pickCell(POSITIONS, null, VIEW, { x: 200, y: 100 })).toBe(0);
    expect(pickCell(POSITIONS, null, VIEW, { x: 100, y: 100 })).toBe(1);
    expect(pickCell(POSITIONS, null, VIEW, { x: 300, y: 50 })).toBe(2);
  });

  it("finds nothing in empty space", () => {
    expect(pickCell(POSITIONS, null, VIEW, { x: 20, y: 180 })).toBeNull();
  });

  it("takes the nearest when two are close", () => {
    // Just right of centre: cell 0 at x=200 beats cell 1 at x=100.
    expect(pickCell(POSITIONS, null, VIEW, { x: 203, y: 100 })).toBe(0);
  });

  it("does not identify a cell that is filtered off the map", () => {
    const hidden = new Float32Array([0, 1, 1]);
    expect(pickCell(POSITIONS, hidden, VIEW, { x: 200, y: 100 })).toBeNull();
    expect(pickCell(POSITIONS, hidden, VIEW, { x: 100, y: 100 })).toBe(1);
  });

  it("follows the map when it is zoomed and panned", () => {
    // Zoom 2 puts the centre cell at clip 0 still, but the one at -0.5 moves to
    // clip -1, which is the left edge.
    const zoomed = { ...VIEW, zoom: 2 };
    expect(pickCell(POSITIONS, null, zoomed, { x: 0, y: 100 })).toBe(1);
    expect(pickCell(POSITIONS, null, zoomed, { x: 100, y: 100 })).toBeNull();

    const panned = { ...VIEW, translate: [0.5, 0] as [number, number] };
    expect(pickCell(POSITIONS, null, panned, { x: 300, y: 100 })).toBe(0);
  });

  it("respects the radius it is given", () => {
    expect(pickCell(POSITIONS, null, VIEW, { x: 215, y: 100 }, 8)).toBeNull();
    expect(pickCell(POSITIONS, null, VIEW, { x: 215, y: 100 }, 20)).toBe(0);
  });

  it("finds nothing before the canvas has a size", () => {
    const unsized = { ...VIEW, width: 0, height: 0 };
    expect(pickCell(POSITIONS, null, unsized, { x: 0, y: 0 })).toBeNull();
  });
});

const CLUSTERS = [
  { ordinal: 0, cluster_id: "Cortex", name: "Cortex (maturation)" },
  { ordinal: 1, cluster_id: "Xylem", name: null },
];

function cell(replicate: string | null, facets: Record<string, string> | null) {
  return { replicate, facets } as Pick<CellArraysRow, "replicate" | "facets">;
}

describe("describeCell", () => {
  const cells = [
    cell("pFACT", { nn_source: "shahan", transgene_pos: "True" }),
    cell("Col-0", null),
    cell(null, null),
  ];
  const ordinals = new Uint8Array([0, 1, 255]);

  it("names the cell type from the catalogue, preferring its curated name", () => {
    expect(describeCell(0, cells, ordinals, CLUSTERS)?.cellType)
      .toBe("Cortex (maturation)");
  });

  it("falls back to the cluster's own id when it has no curated name", () => {
    expect(describeCell(1, cells, ordinals, CLUSTERS)?.cellType).toBe("Xylem");
  });

  it("says a cell has no cell type rather than inventing one", () => {
    expect(describeCell(2, cells, ordinals, CLUSTERS)?.cellType).toBe("No cell type");
  });

  it("names the genotype and where the label was transferred from", () => {
    expect(describeCell(0, cells, ordinals, CLUSTERS)?.detail)
      .toBe("pFACT · label from shahan");
  });

  it("says only what the cell carries", () => {
    expect(describeCell(1, cells, ordinals, CLUSTERS)?.detail).toBe("Col-0");
    expect(describeCell(2, cells, ordinals, CLUSTERS)?.detail).toBe("");
  });

  it("returns nothing for a cell that is not there", () => {
    expect(describeCell(99, cells, ordinals, CLUSTERS)).toBeNull();
  });
});

describe("isClick", () => {
  it("counts a press and release in place, or nearly, as a click", () => {
    expect(isClick({ x: 10, y: 10 }, { x: 10, y: 10 })).toBe(true);
    expect(isClick({ x: 10, y: 10 }, { x: 13, y: 12 })).toBe(true);
  });

  it("counts a press that travelled as a pan, not a click", () => {
    expect(isClick({ x: 10, y: 10 }, { x: 30, y: 10 })).toBe(false);
  });
});
