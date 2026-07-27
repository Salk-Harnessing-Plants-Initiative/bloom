/**
 * Unit tests for the pure advanced-search helpers. `runAdvancedSearch` is a thin
 * wrapper over one RPC call and is covered by the RPC's integration tests; these
 * lock the parsing and the URL round-trip that carry filters between pages.
 */

import { describe, it, expect } from "vitest";
import {
  parseBarcodes,
  filtersEmpty,
  overCapField,
  filtersToParams,
  paramsToFilters,
  MAX_FILTER_ENTRIES,
  AdvancedFilters,
} from "./cyl-plant-search";

const filters = (over: Partial<AdvancedFilters> = {}): AdvancedFilters => ({
  barcodes: [],
  accessionIds: [],
  speciesIds: [],
  experimentIds: [],
  ...over,
});

describe("parseBarcodes", () => {
  it("splits on commas", () => {
    expect(parseBarcodes("A1,B2,C3")).toEqual(["A1", "B2", "C3"]);
  });

  it("splits on newlines", () => {
    expect(parseBarcodes("A1\nB2\nC3")).toEqual(["A1", "B2", "C3"]);
  });

  it("splits on mixed separators and extra whitespace", () => {
    expect(parseBarcodes("A1, B2\n\n  C3 ,")).toEqual(["A1", "B2", "C3"]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseBarcodes("")).toEqual([]);
    expect(parseBarcodes("   \n  ")).toEqual([]);
  });

  it("keeps a barcode's underscores (they are literal, not wildcards)", () => {
    expect(parseBarcodes("SOY_W1_001")).toEqual(["SOY_W1_001"]);
  });
});

describe("filtersEmpty", () => {
  it("is true when no field has anything", () => {
    expect(filtersEmpty(filters())).toBe(true);
  });

  it.each([
    ["barcodes", filters({ barcodes: ["A1"] })],
    ["accessionIds", filters({ accessionIds: [1] })],
    ["speciesIds", filters({ speciesIds: [1] })],
    ["experimentIds", filters({ experimentIds: [1] })],
  ])("is false when only %s is set", (_name, f) => {
    expect(filtersEmpty(f)).toBe(false);
  });
});

describe("filtersToParams / paramsToFilters", () => {
  it("round-trips every field", () => {
    const original = filters({
      barcodes: ["A1", "B2"],
      accessionIds: [1, 2],
      speciesIds: [3],
      experimentIds: [4, 5],
    });
    expect(paramsToFilters(new URLSearchParams(filtersToParams(original).toString()))).toEqual(
      original,
    );
  });

  it("omits empty fields from the URL entirely", () => {
    expect(filtersToParams(filters({ speciesIds: [3] })).toString()).toBe("sp=3");
  });

  it("round-trips empty filters", () => {
    expect(paramsToFilters(new URLSearchParams(""))).toEqual(filters());
  });

  it("drops non-numeric and non-positive ids from a hand-edited URL", () => {
    const f = paramsToFilters(new URLSearchParams("acc=1,abc,-2,0,3"));
    expect(f.accessionIds).toEqual([1, 3]);
  });

  it("reads an oversized list back whole rather than trimming it", () => {
    // Trimming here would drop barcodes that then never get searched and never
    // appear under "not found" — overCapField refuses the search instead.
    const many = Array.from({ length: MAX_FILTER_ENTRIES + 50 }, (_, i) => `B${i}`).join(",");
    expect(paramsToFilters(new URLSearchParams(`barcodes=${many}`)).barcodes).toHaveLength(
      MAX_FILTER_ENTRIES + 50,
    );
  });
});

describe("overCapField", () => {
  const over = (n: number) => Array.from({ length: n }, (_, i) => `B${i}`);

  it("passes a search within the cap", () => {
    expect(overCapField(filters({ barcodes: over(MAX_FILTER_ENTRIES) }))).toBeNull();
  });

  it("names the field that went over", () => {
    expect(overCapField(filters({ barcodes: over(MAX_FILTER_ENTRIES + 1) }))).toBe("barcodes");
  });

  it.each([
    ["accessions", "accessionIds"],
    ["species", "speciesIds"],
    ["experiments", "experimentIds"],
  ])("catches an oversized %s list", (name, key) => {
    const ids = Array.from({ length: MAX_FILTER_ENTRIES + 1 }, (_, i) => i + 1);
    expect(overCapField(filters({ [key]: ids } as any))).toBe(name);
  });

  it("passes an empty search", () => {
    expect(overCapField(filters())).toBeNull();
  });
});
