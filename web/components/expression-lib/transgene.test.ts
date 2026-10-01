import { describe, expect, it } from "vitest";

import { topGroups, transgeneBadge, transgeneByCluster } from "./transgene";

describe("transgeneByCluster", () => {
  it("counts each cluster's transgene-positive cells out of all its cells", () => {
    const counts = transgeneByCluster([
      { cluster_ordinal: 0, facets: { transgene_pos: "True" } },
      { cluster_ordinal: 0, facets: { transgene_pos: "False" } },
      { cluster_ordinal: 1, facets: { transgene_pos: "True" } },
      { cluster_ordinal: 1, facets: { transgene_pos: "True" } },
      { cluster_ordinal: 2, facets: null },
    ]);
    expect(counts?.get(0)).toEqual({ positive: 1, total: 2 });
    expect(counts?.get(1)).toEqual({ positive: 2, total: 2 });
    expect(counts?.get(2)).toEqual({ positive: 0, total: 1 });
  });

  it("is null for a dataset whose cells record no transgene status", () => {
    expect(transgeneByCluster([{ cluster_ordinal: 0, facets: null }])).toBeNull();
    expect(transgeneByCluster([{ cluster_ordinal: 0, facets: { sample: "Col-0" } }])).toBeNull();
  });
});

describe("transgeneBadge", () => {
  it("says how many, and nothing for none", () => {
    expect(transgeneBadge(82)).toBe("82 transgene+");
    expect(transgeneBadge(0)).toBeNull();
  });
});

describe("topGroups", () => {
  it("lists the groups with the most transgene-positive cells, most first", () => {
    const groups = [
      { name: "Xylem", positive: 13 },
      { name: "Phellem", positive: 82 },
      { name: "Phloem", positive: 0 },
      { name: "Columella", positive: 40 },
      { name: "Pericycle", positive: 26 },
    ];
    expect(topGroups(groups).map((g) => g.name)).toEqual(["Phellem", "Columella", "Pericycle"]);
    expect(topGroups(groups, 10).map((g) => g.name)).not.toContain("Phloem");
  });
});
