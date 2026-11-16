import { describe, expect, it } from "vitest";

import { browserConfig } from "./config";
import { launchTarget } from "./launch";

const params = (query: string) => new URLSearchParams(query);

describe("launchTarget", () => {
  it("opens the genes page's reference at its gene", () => {
    expect(launchTarget(params("reference=Athaliana.Col-0.HPIv02&gene=AT1G01010"))).toEqual({
      assembly: "Athaliana.Col-0.HPIv02",
      loc: "AT1G01010",
    });
  });

  it("opens a Bloom genome version at a gene or region", () => {
    expect(launchTarget(params("genome=at.col0.v2&loc=Chr1:100..200"))).toEqual({
      assembly: "at.col0.v2",
      loc: "Chr1:100..200",
    });
  });

  it("prefers the genome link when a URL carries both", () => {
    expect(launchTarget(params("genome=at.col0.v2&loc=AT1G01010&reference=R&gene=G"))?.assembly).toBe(
      "at.col0.v2",
    );
  });

  it.each([
    "",
    "reference=Athaliana.Col-0.HPIv02",
    "gene=AT1G01010",
    "reference=%20&gene=X",
    "genome=at.col0.v2",
    "loc=Chr1:1..2",
    "genome=at.col0.v2&gene=X",
  ])(
    "opens nothing for %j",
    (query) => {
      expect(launchTarget(params(query))).toBeNull();
    },
  );
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
});
