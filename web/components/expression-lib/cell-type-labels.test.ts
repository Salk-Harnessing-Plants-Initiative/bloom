import { describe, expect, it } from "vitest";

import {
  cellTypeLabelKeys,
  formatShares,
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
  it("gives each source's commonest labels with their share of the cluster", () => {
    const input = cells(0, [
      ...Array(7).fill({ atlas: "Columella" }),
      ...Array(2).fill({ atlas: "Lateral root cap" }),
      { atlas: "Xylem" },
    ]);
    expect(predictedCellTypes(input, ["atlas"]).get(0)).toEqual([
      { key: "atlas", labels: [
        { label: "Columella", share: 0.7 },
        { label: "Lateral root cap", share: 0.2 },
      ] },
    ]);
  });

  it("leaves out labels under a tenth of the cluster", () => {
    const input = cells(0, [...Array(19).fill({ atlas: "Columella" }), { atlas: "Xylem" }]);
    expect(predictedCellTypes(input, ["atlas"]).get(0)?.[0].labels.map((l) => l.label)).toEqual([
      "Columella",
    ]);
  });

  it("counts shares out of every cell in the cluster, labelled or not", () => {
    const input = [...cells(0, [{ atlas: "Columella" }]), { cluster_ordinal: 0, facets: null }];
    expect(predictedCellTypes(input, ["atlas"]).get(0)?.[0].labels).toEqual([
      { label: "Columella", share: 0.5 },
    ]);
  });

  it("keeps each cluster and source apart, in the dataset's source order", () => {
    const input = [
      ...cells(0, [{ curated: "Phellem", atlas: "Columella" }]),
      ...cells(1, [{ curated: "Xylem", atlas: "Xylem" }]),
    ];
    const out = predictedCellTypes(input, ["curated", "atlas"]);
    expect(out.get(0)?.map((s) => s.key)).toEqual(["curated", "atlas"]);
    expect(out.get(1)?.[0].labels[0].label).toBe("Xylem");
  });

  it("is empty when the dataset marks no cell types", () => {
    expect(predictedCellTypes(cells(0, [{ atlas: "Columella" }]), []).size).toBe(0);
  });
});

describe("formatShares and sourceName", () => {
  it("writes shares as whole percentages and drops it when every cell agrees", () => {
    expect(formatShares([{ label: "Columella", share: 0.794 }, { label: "LRC", share: 0.18 }]))
      .toBe("Columella 79%, LRC 18%");
    expect(formatShares([{ label: "Young phellem", share: 1 }])).toBe("Young phellem");
  });

  it("turns a label key into words", () => {
    expect(sourceName("periderm_atlas")).toBe("Periderm atlas");
  });
});
