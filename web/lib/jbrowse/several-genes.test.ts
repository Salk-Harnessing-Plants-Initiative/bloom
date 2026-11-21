import { describe, expect, it } from "vitest";

import type { GenePlace } from "./gene-links";
import {
  MAX_PICKS,
  MIN_REGION_SHARE,
  groupRegions,
  highlightsFor,
  lociText,
  missingNote,
  picksLink,
  picksView,
  readPicks,
  tooManyNote,
} from "./several-genes";

const at = (gene: string, contig: string, start: number, end: number, name: string | null = null): GenePlace => ({
  gene,
  name,
  assembly: "tair10.v1",
  label: "tair10 v1",
  contig,
  start,
  end,
});

describe("readPicks", () => {
  it("reads the genes in order, trimmed, without blanks or repeats", () => {
    expect(readPicks(" AT1G01010, ,AT1G01020,AT1G01010 ,")).toEqual({
      genes: ["AT1G01010", "AT1G01020"],
      tooMany: null,
    });
  });

  it("reads nothing from no parameter", () => {
    expect(readPicks(undefined)).toEqual({ genes: [], tooMany: null });
    expect(readPicks("")).toEqual({ genes: [], tooMany: null });
  });

  it("accepts the limit, repeats not counted", () => {
    const genes = Array.from({ length: MAX_PICKS }, (_, i) => `G${i}`);
    expect(readPicks([...genes, "G0", "G1"].join(",")).genes).toHaveLength(MAX_PICKS);
  });

  it("refuses more than the limit, giving how many", () => {
    const genes = Array.from({ length: MAX_PICKS + 1 }, (_, i) => `G${i}`);
    expect(readPicks(genes.join(","))).toEqual({ genes: [], tooMany: MAX_PICKS + 1 });
  });
});

describe("groupRegions", () => {
  it("joins genes on one contig within 1 Mb into one padded region", () => {
    const regions = groupRegions([at("A", "Chr1", 100_001, 102_000), at("B", "Chr1", 300_001, 301_000)]);
    // Span 100,001..301,000 is 201,000 bp; 10% padding is 20,100 bp each side.
    expect(regions).toEqual([{ contig: "Chr1", start: 79_901, end: 321_100, genes: ["A", "B"] }]);
  });

  it("chains genes each within 1 Mb of the one before", () => {
    const regions = groupRegions([
      at("A", "Chr1", 1_000_001, 1_001_000),
      at("B", "Chr1", 1_900_001, 1_901_000),
      at("C", "Chr1", 2_800_001, 2_801_000),
    ]);
    expect(regions.map((r) => r.genes)).toEqual([["A", "B", "C"]]);
  });

  it("keeps genes further apart than 1 Mb as separate regions", () => {
    const regions = groupRegions([at("A", "Chr1", 1_000_001, 1_001_000), at("B", "Chr1", 2_001_001, 2_002_000)]);
    expect(regions.map((r) => r.genes)).toEqual([["A"], ["B"]]);
  });

  it("joins a gene exactly 1 Mb after the one before", () => {
    const regions = groupRegions([at("A", "Chr1", 1_000_001, 1_001_000), at("B", "Chr1", 2_001_000, 2_002_000)]);
    expect(regions.map((r) => r.genes)).toEqual([["A", "B"]]);
  });

  it("keeps genes on other contigs separate, in contig then position order", () => {
    const regions = groupRegions([at("C", "Chr3", 10_001, 11_000), at("A", "Chr1", 50_001, 51_000), at("B", "Chr10", 1, 900)]);
    expect(regions.map((r) => r.contig)).toEqual(["Chr1", "Chr3", "Chr10"]);
  });

  it("never pads before the contig's start", () => {
    expect(groupRegions([at("A", "Chr1", 5, 1_004)])[0].start).toBe(1);
  });

  it("joins overlapping genes", () => {
    const regions = groupRegions([at("A", "Chr1", 1_001, 5_000), at("B", "Chr1", 3_001, 4_000)]);
    expect(regions).toHaveLength(1);
    expect(regions[0].end).toBe(5_000 + 400);
  });

  it("gives nothing for no genes", () => {
    expect(groupRegions([])).toEqual([]);
  });

  it("never pads past the contig's end when its length is known", () => {
    expect(groupRegions([at("A", "Chr1", 990_001, 999_900)], { Chr1: 1_000_000 })[0].end).toBe(1_000_000);
    expect(groupRegions([at("A", "Chr1", 990_001, 999_900)])[0].end).toBe(1_000_890);
  });

  it("merges regions whose padding makes them touch", () => {
    // 1,000,001 bp apart, so not joined; each padded by 500,000 bp, so they meet.
    const regions = groupRegions([at("A", "Chr1", 1_000_001, 6_000_000), at("B", "Chr1", 7_000_001, 12_000_000)]);
    expect(regions).toEqual([{ contig: "Chr1", start: 500_001, end: 12_500_000, genes: ["A", "B"] }]);
  });

  it("widens a small region to a share of the whole, so it is not lost beside a large one", () => {
    const regions = groupRegions([at("A", "Chr1", 1_000_001, 6_000_000), at("B", "Chr3", 1_000_001, 1_001_000)]);
    const total = 6_000_000 + 1_200;
    expect(regions[1]).toEqual({ contig: "Chr3", start: 940_489, end: 1_060_512, genes: ["B"] });
    expect(regions[1].end - regions[1].start + 1).toBe(Math.ceil(total * MIN_REGION_SHARE));
  });
});

describe("picksView", () => {
  it("gives the regions as one location and a highlight per gene", () => {
    const places = [at("A", "Chr1", 100_001, 102_000), at("B", "Chr1", 300_001, 301_000)];
    expect(picksView(places, { Chr1: 310_000 })).toEqual({
      loc: "Chr1:79901..310000",
      highlight: highlightsFor(places),
    });
  });
});

describe("lociText", () => {
  it("lists the regions as JBrowse reads several locations", () => {
    expect(
      lociText([
        { contig: "Chr1", start: 1, end: 100, genes: ["A"] },
        { contig: "Chr3", start: 50, end: 900, genes: ["B"] },
      ]),
    ).toBe("Chr1:1..100 Chr3:50..900");
  });
});

describe("highlightsFor", () => {
  it("marks each gene by name, in JBrowse's zero-based coordinates", () => {
    expect(highlightsFor([at("AT1G01010", "Chr1", 3_631, 5_899, "NAC001"), at("AT1G01020", "Chr1", 6_788, 9_130)])).toEqual([
      JSON.stringify({ refName: "Chr1", start: 3_630, end: 5_899, assemblyName: "tair10.v1", label: "NAC001" }),
      JSON.stringify({ refName: "Chr1", start: 6_787, end: 9_130, assemblyName: "tair10.v1", label: "AT1G01020" }),
    ]);
  });
});

describe("the page's notes", () => {
  it("names one gene left out", () => {
    expect(missingNote(["AT9G99999"], "tair10.v1")).toBe(
      "AT9G99999 is not among tair10.v1's genes, so it was left out.",
    );
  });

  it("names several genes left out", () => {
    expect(missingNote(["X1", "X2"], "tair10.v1")).toBe("X1, X2 are not among tair10.v1's genes, so they were left out.");
  });

  it("gives the limit and how many were asked for", () => {
    expect(tooManyNote(25)).toBe(`Open at most ${MAX_PICKS} genes at once; this link has 25.`);
  });
});

describe("picksLink", () => {
  it("opens the genome with the picks, escaped", () => {
    expect(picksLink("tair10.v1", ["AT1G01010", "a&b"])).toBe("/app/jbrowse?genome=tair10.v1&genes=AT1G01010%2Ca%26b");
  });
});
