import { describe, expect, it } from "vitest";

import { DENSITY_WINDOW_BP, densityBedGraph, densityTrack, densityTrackId } from "./gene-density";

describe("densityBedGraph", () => {
  it("counts genes per 100 kb window by where they start, zero-based half-open", () => {
    const text = densityBedGraph([
      { contig: "Chr1", start: 1 },
      { contig: "Chr1", start: 100_000 },
      { contig: "Chr1", start: 100_001 },
      { contig: "Chr1", start: 350_000 },
    ]);
    expect(text).toBe(
      ["Chr1\t0\t100000\t2", "Chr1\t100000\t200000\t1", "Chr1\t200000\t300000\t0", "Chr1\t300000\t400000\t1", ""].join("\n"),
    );
  });

  it("counts a gene that crosses a window edge in the window it starts in", () => {
    expect(densityBedGraph([{ contig: "Chr1", start: 99_000 }])).toBe("Chr1\t0\t100000\t1\n");
  });

  it("lists contigs in natural order, each from its first window", () => {
    const lines = densityBedGraph([
      { contig: "Chr10", start: 5 },
      { contig: "Chr2", start: 250_001 },
      { contig: "Chr1", start: 5 },
    ]).trim().split("\n");
    expect(lines.map((l) => l.split("\t").slice(0, 2).join(" "))).toEqual(["Chr1 0", "Chr2 0", "Chr2 100000", "Chr2 200000", "Chr10 0"]);
  });

  it("gives nothing for no genes", () => {
    expect(densityBedGraph([])).toBe("");
  });

  it("uses 100 kb windows", () => {
    expect(DENSITY_WINDOW_BP).toBe(100_000);
  });
});

describe("densityTrack", () => {
  it("reads the build's density route as bedGraph, drawn in Bloom's green", () => {
    expect(densityTrack("tair10.v1", 4, "tair10 v1")).toEqual({
      type: "QuantitativeTrack",
      trackId: densityTrackId("tair10.v1"),
      name: "tair10 v1 gene density (genes per 100 kb)",
      assemblyNames: ["tair10.v1"],
      adapter: { type: "BedGraphAdapter", bedGraphLocation: { uri: "/app/jbrowse/density/4", locationType: "UriLocation" } },
      displays: [
        {
          type: "LinearWiggleDisplay",
          displayId: `${densityTrackId("tair10.v1")}-LinearWiggleDisplay`,
          renderers: { XYPlotRenderer: { type: "XYPlotRenderer", color: "#4d7c0f" } },
        },
      ],
    });
  });
});
