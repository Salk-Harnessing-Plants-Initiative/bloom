import { describe, expect, it } from "vitest";

import {
  cellTypeLabels,
  combinedRow,
  countFlagged,
  memberNames,
  countFocused,
  countLevels,
  countNoValue,
  DATASET_KEY,
  datasetRow,
  describeFocus,
  focusIsChosen,
  NO_VALUE_ALPHA,
  normalisePositions,
  packColours,
  packFocus,
  packVisibility,
  palette,
  pointValues,
  type LabelRow,
} from "./joint-map";

// Five points: two from the atlas, three from MYB41. Cell type is the atlas's own
// and was transferred to the query, except for its last point; transgene status
// is only known for the query.
const DATASETS = datasetRow(
  [{ ordinal: 0, name: "atlas" }, { ordinal: 1, name: "MYB41" }],
  [0, 0, 1, 1, 1],
);
const CELL_TYPE: LabelRow = { key: "cell_type", levels: ["Cortex", "Xylem"], codes: [0, 1, 0, 1, -1] };
const TRANSGENE: LabelRow = { key: "transgene_pos", levels: ["False", "True"], codes: [-1, -1, 1, 0, 1] };
const ROWS = [DATASETS, CELL_TYPE, TRANSGENE];

const sets = (entries: [string, number[]][]) =>
  new Map(entries.map(([key, values]) => [key, new Set(values)]));
const NONE = new Map<string, Set<number>>();

describe("datasetRow", () => {
  it("names each dataset in member order and indexes every point into them", () => {
    expect(DATASETS.key).toBe(DATASET_KEY);
    expect(DATASETS.levels).toEqual(["atlas", "MYB41"]);
    expect([...Array.from(DATASETS.codes)]).toEqual([0, 0, 1, 1, 1]);
  });

  it("follows member ordinals that are neither sorted nor from zero", () => {
    const row = datasetRow([{ ordinal: 3, name: "b" }, { ordinal: 1, name: "a" }], [1, 3, 3]);
    expect(row.levels).toEqual(["a", "b"]);
    expect(Array.from(row.codes)).toEqual([0, 1, 1]);
  });
});

describe("normalisePositions", () => {
  it("centres the map and fits its longer side inside the canvas", () => {
    const p = normalisePositions([0, 10], [0, 4]);
    expect(p[0]).toBeCloseTo(-1 / 1.05, 6);
    expect(p[2]).toBeCloseTo(1 / 1.05, 6);
    // The shorter side keeps the same scale, so the map is not stretched.
    expect(p[3] - p[1]).toBeCloseTo((0.4 * 2) / 1.05, 6);
    expect(p[1] + p[3]).toBeCloseTo(0, 6);
  });

  it("puts a single point in the middle rather than dividing by zero", () => {
    expect(Array.from(normalisePositions([5], [5]))).toEqual([0, 0]);
  });
});

describe("palette", () => {
  it("gives every value its own colour, however many there are", () => {
    const colours = palette(40).map((c) => c.join(","));
    expect(new Set(colours).size).toBe(40);
  });

  it("gives a value the same colour whatever the row's length", () => {
    expect(palette(3)).toEqual(palette(30).slice(0, 3));
  });

  it("stays inside the colour range", () => {
    for (const c of palette(40)) for (const v of c) expect(v >= 0 && v <= 1).toBe(true);
  });
});

describe("packColours", () => {
  it("colours a point by its value, and a point with none in faint grey", () => {
    const colours = palette(2);
    const out = packColours(TRANSGENE, colours);
    expect(Array.from(out.slice(8, 12))).toEqual([...colours[1], 1].map(Math.fround));
    expect(out[3]).toBeCloseTo(NO_VALUE_ALPHA, 6);
    expect(out[15]).toBe(1);
  });
});

describe("packVisibility", () => {
  it("hides the points with a hidden value, but never a point with no value", () => {
    expect(Array.from(packVisibility(ROWS, sets([["cell_type", [0]]])))).toEqual([0, 1, 0, 1, 1]);
  });

  it("hides a point that any row hides", () => {
    const hidden = sets([["cell_type", [1]], ["transgene_pos", [1]]]);
    expect(Array.from(packVisibility(ROWS, hidden))).toEqual([1, 0, 0, 0, 0]);
  });

  it("hides nothing when nothing is chosen, or a row's set is empty", () => {
    expect(Array.from(packVisibility(ROWS, NONE))).toEqual([1, 1, 1, 1, 1]);
    expect(Array.from(packVisibility(ROWS, sets([["cell_type", []]])))).toEqual([1, 1, 1, 1, 1]);
  });
});

describe("packFocus", () => {
  it("keeps only the points with a chosen value; a point with no value is not one", () => {
    expect(Array.from(packFocus(ROWS, sets([["transgene_pos", [1]]])))).toEqual([0, 0, 1, 0, 1]);
  });

  it("needs every row with a choice to agree", () => {
    const focused = sets([["transgene_pos", [1]], ["cell_type", [0]]]);
    expect(Array.from(packFocus(ROWS, focused))).toEqual([0, 0, 1, 0, 0]);
  });

  it("takes any of the values chosen in one row", () => {
    expect(Array.from(packFocus(ROWS, sets([["cell_type", [0, 1]]])))).toEqual([1, 1, 1, 1, 0]);
  });

  it("keeps every point when nothing is chosen", () => {
    expect(Array.from(packFocus(ROWS, NONE))).toEqual([1, 1, 1, 1, 1]);
  });
});

describe("focusIsChosen", () => {
  it("is true only once a value is chosen", () => {
    expect(focusIsChosen(NONE)).toBe(false);
    expect(focusIsChosen(sets([["cell_type", []]]))).toBe(false);
    expect(focusIsChosen(sets([["cell_type", [1]]]))).toBe(true);
  });
});

describe("countLevels", () => {
  it("counts every value when nothing is hidden", () => {
    const counts = countLevels(ROWS, NONE);
    expect(counts.get(DATASET_KEY)).toEqual([2, 3]);
    expect(counts.get("cell_type")).toEqual([2, 2]);
    expect(counts.get("transgene_pos")).toEqual([1, 2]);
  });

  it("counts only what the other rows leave showing, so a row's own hiding does not empty it", () => {
    const counts = countLevels(ROWS, sets([["cell_type", [0]]]));
    expect(counts.get("cell_type")).toEqual([2, 2]);
    expect(counts.get("transgene_pos")).toEqual([1, 1]);
    expect(counts.get(DATASET_KEY)).toEqual([1, 2]);
  });

  it("counts a point hidden by two rows in neither of them", () => {
    const counts = countLevels(ROWS, sets([["cell_type", [0]], [DATASET_KEY, [1]]]));
    expect(counts.get("cell_type")).toEqual([1, 1]);
    expect(counts.get(DATASET_KEY)).toEqual([1, 2]);
    expect(counts.get("transgene_pos")).toEqual([0, 0]);
  });
});

describe("countNoValue", () => {
  it("counts the points a row says nothing about", () => {
    expect(countNoValue(TRANSGENE)).toBe(2);
    expect(countNoValue(DATASETS)).toBe(0);
  });
});

describe("countFocused", () => {
  it("counts the points in focus that are still on the map", () => {
    const focused = sets([["transgene_pos", [1]]]);
    expect(countFocused(ROWS, focused, NONE)).toBe(2);
    expect(countFocused(ROWS, focused, sets([["cell_type", [0]]]))).toBe(1);
  });
});

describe("describeFocus", () => {
  it("names the datasets first, then each label with its values", () => {
    const focused = sets([["transgene_pos", [1]], [DATASET_KEY, [1]], ["cell_type", [1, 0]]]);
    expect(describeFocus(ROWS, focused)).toBe("MYB41 and cell_type Cortex or Xylem and transgene_pos True");
  });
});

describe("pointValues", () => {
  it("lists what every row says about a point, null where it says nothing", () => {
    expect(pointValues(2, ROWS)).toEqual([
      { key: DATASET_KEY, value: "MYB41" },
      { key: "cell_type", value: "Cortex" },
      { key: "transgene_pos", value: "True" },
    ]);
    expect(pointValues(0, ROWS)[2]).toEqual({ key: "transgene_pos", value: null });
  });
});

describe("combinedRow", () => {
  // Atlas, nuclei atlas, then two MYB41 cells. The atlas's label was also
  // transferred onto the MYB41 cells; the combined row must not use it there.
  const THREE = datasetRow(
    [{ ordinal: 0, name: "atlas" }, { ordinal: 1, name: "nuclei" }, { ordinal: 2, name: "MYB41" }],
    [0, 0, 1, 2, 2],
  );
  const ATLAS: LabelRow = { key: "atlas_type", levels: ["Cortex", "Xylem"], codes: [0, 1, -1, 1, 1] };
  const NUCLEI: LabelRow = { key: "nuclei_type", levels: ["Cortex (maturation)"], codes: [-1, -1, 0, -1, -1] };
  const QUERY: LabelRow = { key: "nn_label", levels: ["Cortex", "Phloem"], codes: [-1, -1, -1, 1, -1] };

  it("takes each cell's value from its own dataset's label", () => {
    const row = combinedRow("cell type", THREE, new Map([[0, ATLAS], [1, NUCLEI], [2, QUERY]]));
    expect(row.key).toBe("cell type");
    expect(row.levels).toEqual(["Cortex", "Cortex (maturation)", "Phloem", "Xylem"]);
    expect(Array.from(row.codes)).toEqual([0, 3, 1, 2, -1]);
  });

  it("gives one name one value, whichever dataset it comes from", () => {
    const query: LabelRow = { ...QUERY, codes: [-1, -1, -1, 0, 0] };
    const row = combinedRow("cell type", THREE, new Map([[0, ATLAS], [2, query]]));
    expect(Array.from(row.codes)).toEqual([0, 1, -1, 0, 0]);
    expect(row.levels).toEqual(["Cortex", "Xylem"]);
  });

  it("leaves out a value no cell takes, and a dataset with no label has none", () => {
    const row = combinedRow("cell type", THREE, new Map([[2, QUERY]]));
    expect(row.levels).toEqual(["Phloem"]);
    expect(Array.from(row.codes)).toEqual([-1, -1, -1, 0, -1]);
  });
});

describe("cellTypeLabels", () => {
  it("reads which label holds each dataset's cell types, by member", () => {
    const params = { load_options: {}, cell_type_labels: { "2": "nn_label", "0": "shahan_cell_type" } };
    expect(cellTypeLabels(params)).toEqual([[0, "shahan_cell_type"], [2, "nn_label"]]);
  });

  it("ignores anything malformed", () => {
    expect(cellTypeLabels(null)).toEqual([]);
    expect(cellTypeLabels({ cell_type_labels: ["x"] })).toEqual([]);
    expect(cellTypeLabels({ cell_type_labels: { a: "x", "1": 5, "2": "" } })).toEqual([]);
  });
});

describe("memberNames", () => {
  it("reads what the map calls each dataset, by member", () => {
    const names = memberNames({ member_names: { "2": "MYB41 dataset" } });
    expect(names.get(2)).toBe("MYB41 dataset");
    expect(names.has(0)).toBe(false);
  });

  it("is empty when the map names none", () => {
    expect(memberNames({ cell_type_labels: { "0": "x" } }).size).toBe(0);
  });
});

describe("countFlagged", () => {
  it("counts, per value, the points the flag marks", () => {
    // transgene_pos True marks points 2 and 4; point 4 has no cell type.
    expect(countFlagged(CELL_TYPE, TRANSGENE, 1)).toEqual([1, 0]);
    expect(countFlagged(DATASETS, TRANSGENE, 1)).toEqual([0, 2]);
  });

  it("counts nothing for a flag value the row does not have", () => {
    expect(countFlagged(CELL_TYPE, TRANSGENE, -1)).toEqual([0, 0]);
  });
});
