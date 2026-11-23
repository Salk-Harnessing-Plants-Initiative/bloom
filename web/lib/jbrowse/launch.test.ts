import { describe, expect, it } from "vitest";

import { browserConfig } from "./config";
import type { GenePlace } from "./gene-links";
import { DEFAULT_SPAN_BP, bloomTracks, defaultViews, launchPlan, launchTarget, namesNothing, picksToOpen } from "./launch";

const params = (query: string) => new URLSearchParams(query);

describe("launchTarget", () => {
  it("opens the genes page's reference at its gene", () => {
    expect(launchTarget(params("reference=Athaliana.Col-0.HPIv02&gene=AT1G01010"))).toEqual({
      assembly: "Athaliana.Col-0.HPIv02",
      loc: "AT1G01010",
    });
  });

  it("opens a Bloom genome version at a region", () => {
    expect(launchTarget(params("genome=at.col0.v2&loc=Chr1:100..200"))).toEqual({
      assembly: "at.col0.v2",
      loc: "Chr1:100..200",
    });
  });

  it.each(["genome=at.col0.v2", "genome=at.col0.v2&loc=%20"])(
    "opens a Bloom genome version whole when %j gives no location",
    (query) => {
      expect(launchTarget(params(query))).toEqual({ assembly: "at.col0.v2", loc: undefined });
    },
  );

  it("prefers the genome link when a URL carries both", () => {
    expect(launchTarget(params("genome=at.col0.v2&loc=X&reference=R&gene=G"))?.assembly).toBe(
      "at.col0.v2",
    );
  });

  it.each([
    "",
    "reference=Athaliana.Col-0.HPIv02",
    "gene=AT1G01010",
    "reference=%20&gene=X",
    "loc=Chr1:1..2",
    "genome=%20&loc=X",
  ])("opens nothing for %j", (query) => {
    expect(launchTarget(params(query))).toBeNull();
  });
});

describe("launchPlan", () => {
  const loaded = {
    config: { assemblies: [{ name: "Outside.A" }, { name: "at.col0.v2" }], tracks: [] },
    bloomGenomes: ["at.col0.v2"],
    bloomBuilds: { "at.col0.v2": 4 },
  };

  it("shows a Bloom genome's gene track", () => {
    expect(launchPlan({ assembly: "at.col0.v2", loc: "Chr1:1..9" }, loaded)).toEqual({
      assembly: "at.col0.v2",
      loc: "Chr1:1..9",
      tracks: ["at.col0.v2-genes"],
    });
  });

  const two = {
    config: { assemblies: [{ name: "Outside.A" }, { name: "a.v1" }, { name: "b.v2" }], tracks: [] },
    bloomGenomes: ["b.v2", "a.v1"],
    bloomBuilds: { "b.v2": 3, "a.v1": 7 },
  };

  it("opens the most recently built Bloom genome version when the link names nothing", () => {
    expect(launchPlan(null, two, true)).toEqual({ assembly: "a.v1", tracks: ["a.v1-genes"], firstRegion: true });
  });

  it("opens no default for a link it does not recognise", () => {
    expect(launchPlan(null, two, false)).toBeNull();
    expect(launchPlan(null, two)).toBeNull();
  });

  it("opens nothing when the link names nothing and Bloom offers no genome", () => {
    expect(launchPlan(null, { ...loaded, bloomGenomes: [], bloomBuilds: {} }, true)).toBeNull();
  });

  it("adds no track for an outside assembly", () => {
    expect(launchPlan({ assembly: "Outside.A", loc: "G1" }, loaded)?.tracks).toEqual([]);
  });

  it("opens nothing for an assembly the browser does not offer", () => {
    expect(launchPlan({ assembly: "at.col0.v9", loc: "Chr1:1..9" }, loaded)).toBeNull();
  });
});

describe("browserConfig", () => {
  const config = browserConfig();
  const names = config.assemblies.map((a) => a.name);

  it("offers the 23 HPI genomes hosted outside Bloom", () => {
    expect(names).toHaveLength(23);
    expect(new Set(names).size).toBe(23);
    expect(names).toContain("Athaliana.Col-0.HPIv02");
  });

  it("gives every track and text index an assembly the config offers", () => {
    const used = [
      ...(config.tracks as { assemblyNames: string[] }[]),
      ...(config.aggregateTextSearchAdapters as { assemblyNames: string[] }[]),
    ].flatMap((t) => t.assemblyNames);
    expect(used.filter((name) => !names.includes(name))).toEqual([]);
  });

  it("has no outside name a Bloom version could take (lowercase, ending .v<N>)", () => {
    expect(names.filter((n) => n === n.toLowerCase() || /\.v[0-9]+$/.test(n))).toEqual([]);
  });
});

describe("bloomTracks", () => {
  it("is a Bloom genome version's genes only; density waits to be ticked", () => {
    expect(bloomTracks("at.col0.v2")).toEqual(["at.col0.v2-genes"]);
  });
});

describe("defaultViews", () => {
  const views = (chr: string, length: number) => [
    { loc: `${chr}:1..${length}`, tracks: ["a.v1-gene-density"] },
    { loc: `${chr}:1..${Math.min(length, DEFAULT_SPAN_BP)}`, tracks: ["a.v1-sequence", "a.v1-genes"] },
  ];

  it("is the longest chromosome whole with density, then its first 50 kb with sequence and genes", () => {
    expect(DEFAULT_SPAN_BP).toBe(50_000);
    expect(defaultViews("a.v1", { ChrC: 154_478, Chr2: 19_698_289, Chr1: 30_427_671 })).toEqual(
      views("Chr1", 30_427_671),
    );
  });

  it("works for number-named chromosomes", () => {
    expect(defaultViews("a.v1", { Pt: 154_478, "2": 19_698_289, "1": 30_427_671 })).toEqual(views("1", 30_427_671));
  });

  it("shows a short chromosome whole in both, and takes the first of equal ones", () => {
    expect(defaultViews("a.v1", { ctgA: 40_000, ctgB: 40_000 })).toEqual(views("ctgA", 40_000));
  });

  it("is nothing for a genome with no chromosomes", () => {
    expect(defaultViews("a.v1", {})).toBeUndefined();
  });
});

describe("namesNothing", () => {
  it("is true for a link with no browser parameter", () => {
    expect(namesNothing(params(""))).toBe(true);
    expect(namesNothing(params("utm_source=mail"))).toBe(true);
  });

  it.each(["genome=a.v1", "genome=", "reference=X", "gene=G", "genes=G1", "loc=Chr1:1..9", "missing=G"])(
    "is false for %j",
    (query) => {
      expect(namesNothing(params(query))).toBe(false);
    },
  );
});

describe("picksToOpen", () => {
  const plan = { assembly: "at.col0.v2", tracks: [] };
  const gene = { gene: "A", name: null, assembly: "at.col0.v2", label: "at.col0 v2", contig: "Chr1", start: 1, end: 9 };
  const picks = (places: GenePlace[], assembly = "at.col0.v2") => ({ assembly, places, notes: [] });

  it("opens the placed picks on the launched genome", () => {
    expect(picksToOpen(plan, picks([gene]))).toEqual([gene]);
  });

  it("opens the link's location when no pick was placed, or the picks are another genome's", () => {
    expect(picksToOpen(plan, picks([]))).toBeNull();
    expect(picksToOpen(plan, picks([gene], "other.v1"))).toBeNull();
    expect(picksToOpen(plan, null)).toBeNull();
    expect(picksToOpen(null, picks([gene]))).toBeNull();
  });
});
