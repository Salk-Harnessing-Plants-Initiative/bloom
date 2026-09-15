/**
 * Which comparisons count as tested, and how a fold change is printed, are
 * shared by the per-comparison view and the summary, so they are pinned once.
 */

import { describe, expect, it } from "vitest";

import { type DeEntry, formatFoldChange, groupNames, isTested } from "./de-types";

const base: DeEntry = {
  id: 1,
  cluster_id: "Cortex",
  contrast: "pFACT_vs_Col-0",
  group1: "pFACT",
  group2: "Col-0",
  n_group1: 100,
  n_group2: 90,
  n_genes_tested: 15000,
  tested: true,
};

describe("isTested", () => {
  it("is true for a tested comparison and false for a skipped one", () => {
    expect(isTested(base)).toBe(true);
    expect(isTested({ ...base, tested: false, n_genes_tested: 0 })).toBe(false);
  });

  it("counts an older one-vs-rest row, which carries no flag, as tested", () => {
    expect(isTested({ ...base, contrast: null, tested: null, n_genes_tested: null })).toBe(true);
  });

  it("is false when no gene was tested, whatever the flag says", () => {
    expect(isTested({ ...base, tested: null, n_genes_tested: 0 })).toBe(false);
  });
});

describe("formatFoldChange", () => {
  it("prints a finite value to the digits asked", () => {
    expect(formatFoldChange(1.23456, 2)).toBe("1.23");
    expect(formatFoldChange(-0.5, 3)).toBe("-0.500");
  });

  it("prints an infinite one as a signed infinity, and nothing for none", () => {
    expect(formatFoldChange(Infinity, 2)).toBe("+∞");
    expect(formatFoldChange(-Infinity, 2)).toBe("−∞");
    expect(formatFoldChange(NaN, 2)).toBe("");
  });
});

describe("groupNames", () => {
  it("names the two groups, or the cell type and the rest", () => {
    expect(groupNames(base)).toEqual({ a: "pFACT", b: "Col-0" });
    expect(groupNames({ ...base, group1: null, group2: null })).toEqual({ a: "Cortex", b: "the rest" });
  });
});
