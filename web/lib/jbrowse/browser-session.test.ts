import { describe, expect, it, vi } from "vitest";

import type { GenePlace } from "./gene-links";
import {
  contigLengths,
  goToGene,
  groupByGenome,
  openAssemblies,
  openEmptyView,
  openPicks,
  openViews,
  replaceViews,
  searchedGenomes,
  searchScope,
} from "./browser-session";
import { picksView } from "./several-genes";

const view = (type: string, assemblyNames: string[], init?: { assembly: string }) => ({
  type,
  assemblyNames,
  init,
  navToLocString: vi.fn(async () => {}),
  setInit: vi.fn(),
  setHighlight: vi.fn(),
});

const TRACKS = ["b.v2-genes"];

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

  it("searches every Bloom genome the page offers when none is open", () => {
    expect(searchScope([], null, builds)).toEqual([4, 7]);
    expect(searchScope(["Athaliana.Col-0.HPIv02"], null, builds)).toEqual([4, 7]);
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
    await goToGene(session, place("AT1G01010", "b.v2"), TRACKS);
    expect(b.navToLocString).toHaveBeenCalledWith("Chr1:3631..5899", "b.v2");
    expect(a.navToLocString).not.toHaveBeenCalled();
    expect(session.addView).not.toHaveBeenCalled();
  });

  it("moves the latest view when a genome has two, leaving the one above", async () => {
    const whole = view("LinearGenomeView", ["b.v2"]);
    const region = view("LinearGenomeView", ["b.v2"]);
    const session = { views: [whole, region], addView: vi.fn() };
    await goToGene(session, place("AT1G01010", "b.v2"), TRACKS);
    expect(region.navToLocString).toHaveBeenCalledWith("Chr1:3631..5899", "b.v2");
    expect(whole.navToLocString).not.toHaveBeenCalled();
  });

  it("opens a view of the genome, with the given tracks shown, when none is open", async () => {
    const session = { views: [view("LinearGenomeView", ["a.v1"])], addView: vi.fn() };
    await goToGene(session, place("AT1G01010", "b.v2"), TRACKS);
    expect(session.addView).toHaveBeenCalledWith("LinearGenomeView", {
      init: { assembly: "b.v2", loc: "Chr1:3631..5899", tracks: TRACKS },
    });
  });

  it("uses a view still opening for the genome rather than adding another", async () => {
    const opening = view("LinearGenomeView", [], { assembly: "b.v2" });
    const session = { views: [opening], addView: vi.fn() };
    await goToGene(session, place("AT1G01010", "b.v2"), TRACKS);
    expect(opening.navToLocString).toHaveBeenCalledWith("Chr1:3631..5899", "b.v2");
    expect(session.addView).not.toHaveBeenCalled();
  });

  it("fails when the browser cannot add a view", async () => {
    await expect(goToGene({ views: [] }, place("AT1G01010", "b.v2"), TRACKS)).rejects.toThrow(
      "The browser could not open b.v2.",
    );
  });
});

describe("contigLengths", () => {
  it("gives each contig's length from the browser's assembly", async () => {
    const waitForAssembly = vi.fn(async () => ({
      regions: [
        { refName: "Chr1", start: 0, end: 30_427_671 },
        { refName: "ChrM", start: 0, end: 366_924 },
      ],
    }));
    await expect(contigLengths({ views: [], assemblyManager: { waitForAssembly } }, "b.v2")).resolves.toEqual({
      Chr1: 30_427_671,
      ChrM: 366_924,
    });
    expect(waitForAssembly).toHaveBeenCalledWith("b.v2");
  });

  it("gives none when the browser cannot tell", async () => {
    await expect(contigLengths({ views: [] }, "b.v2")).resolves.toEqual({});
    const waitForAssembly = vi.fn(async () => undefined);
    await expect(contigLengths({ views: [], assemblyManager: { waitForAssembly } }, "b.v2")).resolves.toEqual({});
  });
});

describe("openPicks", () => {
  const picks = [
    { ...place("A", "b.v2"), start: 30_400_001, end: 30_427_000 },
    { ...place("B", "b.v2"), contig: "Chr3", start: 10_001, end: 11_000 },
  ];
  const lengths = { Chr1: 30_427_671, Chr3: 23_459_830 };
  const assemblyManager = {
    waitForAssembly: vi.fn(async () => ({
      regions: Object.entries(lengths).map(([refName, end]) => ({ refName, start: 0, end })),
    })),
  };

  it("opens the picks in the genome's open view, kept on its contigs, leaving other views", async () => {
    const a = view("LinearGenomeView", ["a.v1"]);
    const b = view("LinearGenomeView", ["b.v2"]);
    const session = { views: [a, b], addView: vi.fn(), assemblyManager };
    await openPicks(session, "b.v2", picks, TRACKS);
    const { loc, highlight } = picksView(picks, lengths);
    expect(loc).toContain("..30427671");
    expect(b.setHighlight).toHaveBeenCalledWith([]);
    expect(b.setInit).toHaveBeenCalledWith({ assembly: "b.v2", loc, highlight });
    expect(a.setInit).not.toHaveBeenCalled();
    expect(session.addView).not.toHaveBeenCalled();
  });

  it("adds a view of the genome, with its tracks, when none is open", async () => {
    const session = { views: [view("LinearGenomeView", ["a.v1"])], addView: vi.fn(), assemblyManager };
    await openPicks(session, "b.v2", picks, TRACKS);
    const { loc, highlight } = picksView(picks, lengths);
    expect(session.addView).toHaveBeenCalledWith("LinearGenomeView", {
      init: { assembly: "b.v2", loc, highlight, tracks: TRACKS },
    });
  });
});

describe("searchedGenomes", () => {
  it("searches every genome while only the default view is open", () => {
    expect(searchedGenomes(["a.v1"], "a.v1")).toEqual([]);
  });

  it("searches the open genomes once the member opens another, or with no default", () => {
    expect(searchedGenomes(["a.v1", "b.v2"], "a.v1")).toEqual(["a.v1", "b.v2"]);
    expect(searchedGenomes(["b.v2"], "a.v1")).toEqual(["b.v2"]);
    expect(searchedGenomes(["a.v1"], null)).toEqual(["a.v1"]);
  });
});

describe("openViews", () => {
  it("adds each view in order, with its region and tracks", () => {
    const addView = vi.fn();
    openViews({ views: [], addView }, "a.v1", [
      { loc: "Chr1:1..30427671", tracks: ["a.v1-gene-density"] },
      { loc: "Chr1:1..50000", tracks: ["a.v1-sequence", "a.v1-genes"] },
    ]);
    expect(addView.mock.calls).toEqual([
      ["LinearGenomeView", { init: { assembly: "a.v1", loc: "Chr1:1..30427671", tracks: ["a.v1-gene-density"] } }],
      ["LinearGenomeView", { init: { assembly: "a.v1", loc: "Chr1:1..50000", tracks: ["a.v1-sequence", "a.v1-genes"] } }],
    ]);
  });

  it("fails when the browser cannot add a view", () => {
    expect(() => openViews({ views: [] }, "a.v1", [{ loc: "Chr1:1..9", tracks: [] }])).toThrow(
      "The browser could not open a.v1.",
    );
  });
});

describe("replaceViews", () => {
  it("closes every open view, then adds the genome's views", () => {
    const old = [view("LinearGenomeView", ["a.v1"]), view("LinearGenomeView", ["b.v2"])];
    const session = { views: [...old], addView: vi.fn(), removeView: vi.fn() };
    replaceViews(session, "c.v1", [{ loc: "Chr1:1..9", tracks: ["c.v1-genes"] }]);
    expect(session.removeView.mock.calls).toEqual([[old[0]], [old[1]]]);
    expect(session.addView).toHaveBeenCalledWith("LinearGenomeView", {
      init: { assembly: "c.v1", loc: "Chr1:1..9", tracks: ["c.v1-genes"] },
    });
    expect(session.removeView.mock.invocationCallOrder[1]).toBeLessThan(session.addView.mock.invocationCallOrder[0]);
  });

  it("fails, closing nothing, when the browser cannot close views", () => {
    const session = { views: [view("LinearGenomeView", ["a.v1"])], addView: vi.fn() };
    expect(() => replaceViews(session, "c.v1", [{ loc: "Chr1:1..9", tracks: [] }])).toThrow(
      "The browser could not open c.v1.",
    );
    expect(session.addView).not.toHaveBeenCalled();
  });
});

describe("openEmptyView", () => {
  it("adds a linear view that asks for a genome and location", () => {
    const addView = vi.fn();
    openEmptyView({ views: [], addView });
    expect(addView).toHaveBeenCalledWith("LinearGenomeView", {});
  });
});
