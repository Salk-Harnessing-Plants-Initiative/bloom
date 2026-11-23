import { describe, expect, it } from "vitest";

import { genomeChoices, geneTracksOf, viewsForGenome } from "./genome-choice";

const loaded = {
  config: {
    assemblies: [
      { name: "Zmays.B73.HPIv1" },
      { name: "Athaliana.Col-0.HPIv02", displayName: "Arabidopsis HPIv02" },
      { name: "a.v1", displayName: "a v1 (Arabidopsis thaliana)" },
    ],
    tracks: [
      { type: "FeatureTrack", trackId: "Athaliana.Col-0.HPIv02.gene.sorted.gff3", assemblyNames: ["Athaliana.Col-0.HPIv02"] },
      { type: "FeatureTrack", trackId: "Athaliana.Col-0.HPIv02.long_rnaseq.stringtie.sorted.gff3", assemblyNames: ["Athaliana.Col-0.HPIv02"] },
      { type: "AlignmentsTrack", trackId: "Athaliana.Col-0.HPIv02.long_rnaseq", assemblyNames: ["Athaliana.Col-0.HPIv02"] },
      { type: "FeatureTrack", trackId: "Zmays.B73.HPIv1.genes.sorted.gff3", assemblyNames: ["Zmays.B73.HPIv1"] },
      { type: "FeatureTrack", trackId: "a.v1-genes", assemblyNames: ["a.v1"] },
    ],
  },
  bloomGenomes: ["a.v1"],
  bloomBuilds: { "a.v1": 4 },
};

describe("genomeChoices", () => {
  it("lists Bloom's genome versions, then the outside assemblies by name, each with its label", () => {
    expect(genomeChoices(loaded)).toEqual({
      bloom: [{ name: "a.v1", label: "a v1 (Arabidopsis thaliana)" }],
      outside: [
        { name: "Athaliana.Col-0.HPIv02", label: "Arabidopsis HPIv02" },
        { name: "Zmays.B73.HPIv1", label: "Zmays.B73.HPIv1" },
      ],
    });
  });
});

describe("geneTracksOf", () => {
  it("is an outside assembly's gene tracks, not its RNA-seq tracks", () => {
    expect(geneTracksOf(loaded.config, "Athaliana.Col-0.HPIv02")).toEqual(["Athaliana.Col-0.HPIv02.gene.sorted.gff3"]);
    expect(geneTracksOf(loaded.config, "Zmays.B73.HPIv1")).toEqual(["Zmays.B73.HPIv1.genes.sorted.gff3"]);
  });
});

describe("viewsForGenome", () => {
  const lengths = { Pt: 154_478, Chr1: 30_427_671, Chr2: 19_698_289 };

  it("gives a Bloom genome its two default views of the longest chromosome", () => {
    expect(viewsForGenome(loaded, "a.v1", lengths)).toEqual([
      { loc: "Chr1:1..30427671", tracks: ["a.v1-gene-density"] },
      { loc: "Chr1:1..50000", tracks: ["a.v1-sequence", "a.v1-genes"] },
    ]);
  });

  it("gives an outside assembly one view of its longest chromosome's first 50 kb with its genes", () => {
    expect(viewsForGenome(loaded, "Athaliana.Col-0.HPIv02", lengths)).toEqual([
      { loc: "Chr1:1..50000", tracks: ["Athaliana.Col-0.HPIv02.gene.sorted.gff3"] },
    ]);
  });

  it("is nothing when the chromosomes are unknown", () => {
    expect(viewsForGenome(loaded, "a.v1", {})).toBeUndefined();
    expect(viewsForGenome(loaded, "Zmays.B73.HPIv1", {})).toBeUndefined();
  });
});
