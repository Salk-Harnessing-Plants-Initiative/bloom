import { describe, expect, it } from "vitest";

import { browserConfig } from "./config";
import { launchPlan, launchTarget } from "./launch";

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
  };

  it("shows a Bloom genome's gene track", () => {
    expect(launchPlan({ assembly: "at.col0.v2", loc: "Chr1:1..9" }, loaded)).toEqual({
      assembly: "at.col0.v2",
      loc: "Chr1:1..9",
      tracks: ["at.col0.v2-genes"],
    });
  });

  it("adds no track for an outside assembly", () => {
    expect(launchPlan({ assembly: "Outside.A", loc: "G1" }, loaded)?.tracks).toEqual([]);
  });

  it("opens nothing for an assembly the browser does not offer", () => {
    expect(launchPlan({ assembly: "at.col0.v9", loc: "Chr1:1..9" }, loaded)).toBeNull();
    expect(launchPlan(null, loaded)).toBeNull();
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
