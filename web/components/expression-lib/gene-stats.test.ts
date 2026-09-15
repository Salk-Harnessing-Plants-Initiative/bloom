import { describe, expect, it } from "vitest";

import type { ClusterMarkers } from "./cluster-markers";
import {
  cellTypes,
  colourScale,
  csvText,
  groupCells,
  groupStats,
  MAX_DE_GENES,
  MAX_MARKER_GENES,
  NO_CELL_TYPE,
  NOT_RECORDED,
  splitsOffered,
  startingGenes,
  tableRows,
  violinShape,
} from "./gene-stats";

const idx = (...i: number[]) => Int32Array.from(i);

describe("groupStats", () => {
  it("counts every cell of the group, zeros included", () => {
    const s = groupStats([0, 0, 2, 6], idx(0, 1, 2, 3))!;
    expect(s.n).toBe(4);
    expect(s.expressing).toBe(2);
    expect(s.share).toBe(0.5);
    expect(s.mean).toBe(2);
  });

  it("puts the quartiles and whiskers over every value", () => {
    const s = groupStats([0, 0, 2, 6], idx(0, 1, 2, 3))!;
    expect([s.min, s.q1, s.median, s.q3, s.max]).toEqual([0, 0, 1, 3, 6]);
    expect([s.lowWhisker, s.highWhisker]).toEqual([0, 6]);
  });

  it("stops a whisker at the last value within 1.5 × IQR of the box", () => {
    const s = groupStats([1, 2, 3, 4, 5, 6, 7, 8, 100], idx(0, 1, 2, 3, 4, 5, 6, 7, 8))!;
    expect([s.q1, s.q3]).toEqual([3, 7]);
    expect([s.lowWhisker, s.highWhisker, s.max]).toEqual([1, 8, 100]);
  });

  it("reads only the group's own cells", () => {
    const s = groupStats([5, 0, 1], idx(0, 2))!;
    expect([s.n, s.expressing, s.mean]).toEqual([2, 2, 3]);
  });

  it("is null for a group with no cells", () => {
    expect(groupStats([1, 2], idx())).toBeNull();
  });
});

const CLUSTERS = [
  { ordinal: 1, cluster_id: "X", name: "Xylem", color: "#0000ff" },
  { ordinal: 0, cluster_id: "C", name: null, color: "#ff0000" },
];

describe("cellTypes", () => {
  it("follows the map's order and names an unnamed cluster by its id", () => {
    expect(cellTypes(CLUSTERS)).toEqual([
      { ordinal: 0, name: "C", color: "#ff0000" },
      { ordinal: 1, name: "Xylem", color: "#0000ff" },
    ]);
  });
});

const CELLS = [
  { cluster_ordinal: 0, genotype: "pFACT", facets: { transgene_pos: "True" } },
  { cluster_ordinal: 1, genotype: "Col-0", facets: { transgene_pos: "False" } },
  { cluster_ordinal: 0, genotype: "Col-0", facets: null },
  { cluster_ordinal: 255, genotype: null, facets: null },
];

describe("groupCells", () => {
  const types = cellTypes(CLUSTERS);

  it("groups cells by cell type in the map's order, the ones with none last", () => {
    const groups = groupCells(CELLS, types, "none");
    expect(groups.map((g) => [g.key, Array.from(g.cells)])).toEqual([
      ["C", [0, 2]],
      ["Xylem", [1]],
      [NO_CELL_TYPE, [3]],
    ]);
    expect(groups[0].color).toBe("#ff0000");
    expect(groups[0].part).toBeNull();
  });

  it("splits each cell type by genotype, cells without one as Not recorded", () => {
    const groups = groupCells(CELLS, types, "genotype");
    expect(groups.map((g) => [g.cellType, g.part, Array.from(g.cells)])).toEqual([
      ["C", "Col-0", [2]],
      ["C", "pFACT", [0]],
      ["Xylem", "Col-0", [1]],
      [NO_CELL_TYPE, NOT_RECORDED, [3]],
    ]);
  });

  it("splits by transgene status", () => {
    const groups = groupCells(CELLS, types, "transgene");
    expect(groups.map((g) => [g.cellType, g.part])).toEqual([
      ["C", "Transgene+"],
      ["C", NOT_RECORDED],
      ["Xylem", "Transgene−"],
      [NO_CELL_TYPE, NOT_RECORDED],
    ]);
  });

  it("leaves out a cell type with no cells", () => {
    expect(groupCells([CELLS[1]], types, "none").map((g) => g.key)).toEqual(["Xylem"]);
  });
});

describe("splitsOffered", () => {
  it("offers a split only when some cell records it", () => {
    expect(splitsOffered(CELLS)).toEqual({ genotype: true, transgene: true });
    expect(splitsOffered([{ cluster_ordinal: 0, genotype: null, facets: { sample: "x" } }]))
      .toEqual({ genotype: false, transgene: false });
  });
});

describe("violinShape", () => {
  it("peaks where the values gather, evenly on either side", () => {
    const shape = violinShape([1, 2, 2, 2, 3], idx(0, 1, 2, 3, 4), [0, 4])!;
    const at = (x: number) =>
      shape.reduce((best, p) => (Math.abs(p.value - x) < Math.abs(best.value - x) ? p : best))
        .density;
    expect(at(2)).toBeCloseTo(1, 1);
    expect(at(1)).toBeLessThan(at(2));
    expect(Math.abs(at(1) - at(3))).toBeLessThan(0.05);
  });

  it("is left out with fewer than 3 distinct values", () => {
    expect(violinShape([0, 0, 5], idx(0, 1, 2), [0, 5])).toBeNull();
    expect(violinShape([0, 0, 0], idx(0, 1, 2), [0, 1])).toBeNull();
  });
});

const markers = (...genes: string[]): ClusterMarkers => ({
  top: genes.map((gene) => ({ gene, log2fc: 1, q: 0, pct_1: 1, pct_2: 0 })),
  n_significant: genes.length,
});

describe("startingGenes", () => {
  it("takes each cell type's first marker, then its second, without repeats", () => {
    expect(startingGenes([markers("A", "B"), null, markers("A", "C")], [])).toEqual({
      genes: ["A", "B", "C"],
      source: "markers",
    });
  });

  it("stops at the cap", () => {
    const many = Array.from({ length: 20 }, (_, i) => markers(`G${i}`));
    expect(startingGenes(many, []).genes).toHaveLength(MAX_MARKER_GENES);
  });

  it("falls back to the genes passing the DE cuts in the most cell types", () => {
    const de = Array.from({ length: 20 }, (_, i) => `D${i}`);
    expect(startingGenes([null, markers()], de)).toEqual({
      genes: de.slice(0, MAX_DE_GENES),
      source: "de",
    });
  });

  it("starts empty with neither", () => {
    expect(startingGenes([], [])).toEqual({ genes: [], source: "none" });
  });
});

describe("colourScale", () => {
  const means = [
    [0, 2, 4],
    [1, 1, 0],
    [0, 0, 0],
  ];

  it("scales each gene to its own highest mean", () => {
    const { t, max } = colourScale(means, true);
    expect(t).toEqual([
      [0, 0.5, 1],
      [1, 1, 0],
      [0, 0, 0],
    ]);
    expect(max).toEqual([4, 1, 0]);
  });

  it("puts every gene on one scale when asked", () => {
    const { t, max } = colourScale(means, false);
    expect(t[1]).toEqual([0.25, 0.25, 0]);
    expect(max).toEqual([4, 4, 4]);
  });
});

describe("tableRows and csvText", () => {
  const types = cellTypes(CLUSTERS);
  const groups = groupCells(CELLS, types, "none");
  const values = new Map([
    ["g1", Float32Array.from([0, 6, 2, 0])],
    ["g2", Float32Array.from([1, 1, 1, 1])],
  ]);

  it("has a row per gene and group, leaving out genes not read", () => {
    const rows = tableRows(["g1", "g2", "g3"], values, groups);
    expect(rows).toHaveLength(6);
    expect(rows[0]).toMatchObject({ gene: "g1", cellType: "C", part: null });
    expect(rows[0].stats.mean).toBe(1);
  });

  it("writes a header and one line per row", () => {
    const text = csvText(tableRows(["g1"], values, groups));
    const lines = text.trim().split("\n");
    expect(lines[0]).toBe("gene,cell_type,group,cells,expressing,share_expressing,mean,median,q1,q3");
    expect(lines[1]).toBe("g1,C,,2,1,0.5,1,1,0.5,1.5");
    expect(lines).toHaveLength(4);
  });

  it("quotes a name with a comma or a quote", () => {
    const odd = groupCells(CELLS, cellTypes([{ ordinal: 0, cluster_id: "c", name: 'Cortex, "outer"', color: null }]), "none");
    const text = csvText(tableRows(["g1"], values, odd));
    expect(text.split("\n")[1]).toMatch(/^g1,"Cortex, ""outer""",/);
  });
});
