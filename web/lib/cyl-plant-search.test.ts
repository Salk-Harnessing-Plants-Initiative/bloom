/**
 * Unit tests for the pure advanced-search helpers. `runAdvancedSearch` is a thin
 * wrapper over one RPC call and is covered by the RPC's integration tests; these
 * lock the parsing and the URL round-trip that carry filters between pages.
 */

import { describe, it, expect } from "vitest";
import {
  parseBarcodes,
  filtersEmpty,
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

  it("caps each id field so a crafted URL can't build an unbounded list", () => {
    const many = Array.from({ length: MAX_FILTER_ENTRIES + 50 }, (_, i) => i + 1).join(",");
    expect(paramsToFilters(new URLSearchParams(`acc=${many}`)).accessionIds).toHaveLength(
      MAX_FILTER_ENTRIES,
    );
  });

  it("caps the barcode field the same way", () => {
    const many = Array.from({ length: MAX_FILTER_ENTRIES + 50 }, (_, i) => `B${i}`).join(",");
    expect(paramsToFilters(new URLSearchParams(`barcodes=${many}`)).barcodes).toHaveLength(
      MAX_FILTER_ENTRIES,
    );
  });

  it("keeps a barcode list of exactly the cap intact", () => {
    const many = Array.from({ length: MAX_FILTER_ENTRIES }, (_, i) => `B${i}`).join(",");
    expect(paramsToFilters(new URLSearchParams(`barcodes=${many}`)).barcodes).toHaveLength(
      MAX_FILTER_ENTRIES,
    );
  });
});
