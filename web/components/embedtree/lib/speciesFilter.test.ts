import { describe, it, expect } from "vitest";
import { resolveSpeciesFilter } from "./speciesFilter";

describe("resolveSpeciesFilter", () => {
  it("returns null when nothing is selected (= all species)", () => {
    expect(resolveSpeciesFilter(new Set(), 4)).toBeNull();
  });

  it("returns null when every species is selected (= all, no filter needed)", () => {
    expect(resolveSpeciesFilter(new Set(["rice", "maize"]), 2)).toBeNull();
  });

  it("returns the explicit subset when a strict subset is selected", () => {
    expect(resolveSpeciesFilter(new Set(["rice"]), 4)).toEqual(["rice"]);
  });

  it("sorts the subset for a stable argument", () => {
    expect(resolveSpeciesFilter(new Set(["rice", "arabidopsis"]), 4)).toEqual([
      "arabidopsis",
      "rice",
    ]);
  });

  it("treats size >= total defensively as no filter", () => {
    expect(resolveSpeciesFilter(new Set(["a", "b", "c"]), 2)).toBeNull();
  });
});
