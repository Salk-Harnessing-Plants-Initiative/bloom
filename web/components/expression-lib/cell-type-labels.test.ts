import { describe, expect, it } from "vitest";

import {
  cellTypeLabelKeys,
  predictedCellTypes,
  sourceName,
} from "./cell-type-labels";

function cells(ordinal: number, labels: Record<string, string>[]) {
  return labels.map((facets) => ({ cluster_ordinal: ordinal, facets }));
}

describe("cellTypeLabelKeys", () => {
  it("reads the keys the dataset marks as cell types, in order", () => {
    expect(cellTypeLabelKeys({ cell_type_labels: ["curated", "atlas"] })).toEqual([
      "curated",
      "atlas",
    ]);
  });

  it.each([null, undefined, [], "x", {}, { cell_type_labels: "atlas" }])(
    "is empty for metadata that marks none: %j",
    (metadata) => {
      expect(cellTypeLabelKeys(metadata)).toEqual([]);
    },
  );

  it("drops blanks, non-text and repeats", () => {
    expect(cellTypeLabelKeys({ cell_type_labels: ["atlas", "", " ", 3, "atlas"] })).toEqual([
      "atlas",
    ]);
  });
});

describe("predictedCellTypes", () => {
  it("gives the label most of the cluster's cells carry from each source", () => {
    const input = cells(0, [
      ...Array(7).fill({ atlas: "Columella" }),
      ...Array(2).fill({ atlas: "Lateral root cap" }),
      { atlas: "Xylem" },
    ]);
    expect(predictedCellTypes(input, ["atlas"]).get(0)).toEqual([
      { key: "atlas", label: "Columella" },
    ]);
  });

  it("breaks a tie by name, so the label does not change between loads", () => {
    const input = cells(0, [{ atlas: "Xylem" }, { atlas: "Cortex" }]);
    expect(predictedCellTypes(input, ["atlas"]).get(0)?.[0].label).toBe("Cortex");
  });

  it("skips a source none of the cluster's cells has a label from", () => {
    const input = [...cells(0, [{ curated: "Phellem" }]), { cluster_ordinal: 0, facets: null }];
    expect(predictedCellTypes(input, ["curated", "atlas"]).get(0)).toEqual([
      { key: "curated", label: "Phellem" },
    ]);
  });

  it("keeps each cluster and source apart, in the dataset's source order", () => {
    const input = [
      ...cells(0, [{ curated: "Phellem", atlas: "Columella" }]),
      ...cells(1, [{ curated: "Xylem", atlas: "Xylem" }]),
    ];
    const out = predictedCellTypes(input, ["curated", "atlas"]);
    expect(out.get(0)?.map((s) => s.key)).toEqual(["curated", "atlas"]);
    expect(out.get(1)?.[0].label).toBe("Xylem");
  });

  it("is empty when the dataset marks no cell types", () => {
    expect(predictedCellTypes(cells(0, [{ atlas: "Columella" }]), []).size).toBe(0);
  });
});

describe("sourceName", () => {
  it("turns a label key into words", () => {
    expect(sourceName("periderm_atlas")).toBe("Periderm atlas");
    expect(sourceName("curated")).toBe("Curated");
  });
});
