import { describe, expect, it, vi } from "vitest";

import type { GenePlace } from "./gene-links";
import { goToGene, groupByGenome, openAssemblies, searchScope } from "./browser-session";

const view = (type: string, assemblyNames: string[]) => ({
  type,
  assemblyNames,
  navToLocString: vi.fn(async () => {}),
});

const place = (gene: string, assembly: string, label = assembly): GenePlace => ({
  gene,
  name: null,
  assembly,
  label,
  contig: "Chr1",
  start: 3631,
  end: 5899,
});

describe("openAssemblies", () => {
  it("lists each genome open in a linear view once, in view order", () => {
    const session = {
      views: [
        view("LinearGenomeView", ["b.v1"]),
        view("CircularView", ["c.v1"]),
        view("LinearGenomeView", ["a.v2"]),
        view("LinearGenomeView", ["b.v1"]),
      ],
    };
    expect(openAssemblies(session)).toEqual(["b.v1", "a.v2"]);
  });

  it("lists nothing before JBrowse has a session", () => {
    expect(openAssemblies(null)).toEqual([]);
    expect(openAssemblies({ views: [] })).toEqual([]);
  });
});

describe("searchScope", () => {
  const builds = { "a.v1": 4, "b.v2": 7 };

  it("searches only the picked genome once a gene is picked", () => {
    expect(searchScope(["a.v1", "b.v2"], "b.v2", builds)).toEqual([7]);
  });

  it("searches the Bloom genomes open in the browser", () => {
    expect(searchScope(["a.v1", "Athaliana.Col-0.HPIv02", "b.v2"], null, builds)).toEqual([4, 7]);
  });

  it("searches every Bloom genome when none is open", () => {
    expect(searchScope([], null, builds)).toBeNull();
    expect(searchScope(["Athaliana.Col-0.HPIv02"], null, builds)).toBeNull();
  });
});

describe("groupByGenome", () => {
  it("groups matches by genome, keeping their order", () => {
    const groups = groupByGenome([place("A", "a.v1", "a v1"), place("B", "a.v1", "a v1"), place("C", "b.v2", "b v2")]);
    expect(groups.map((g) => [g.label, g.places.map((p) => p.gene)])).toEqual([
      ["a v1", ["A", "B"]],
      ["b v2", ["C"]],
    ]);
  });
});

describe("goToGene", () => {
  it("moves only the open view of the gene's genome", async () => {
    const a = view("LinearGenomeView", ["a.v1"]);
    const b = view("LinearGenomeView", ["b.v2"]);
    const session = { views: [a, b], addView: vi.fn() };
    await goToGene(session, place("AT1G01010", "b.v2"), "b.v2-genes");
    expect(b.navToLocString).toHaveBeenCalledWith("Chr1:3631..5899", "b.v2");
    expect(a.navToLocString).not.toHaveBeenCalled();
    expect(session.addView).not.toHaveBeenCalled();
  });

  it("opens a view of the genome, with its genes shown, when none is open", async () => {
    const session = { views: [view("LinearGenomeView", ["a.v1"])], addView: vi.fn() };
    await goToGene(session, place("AT1G01010", "b.v2"), "b.v2-genes");
    expect(session.addView).toHaveBeenCalledWith("LinearGenomeView", {
      init: { assembly: "b.v2", loc: "Chr1:3631..5899", tracks: ["b.v2-genes"] },
    });
  });
});
