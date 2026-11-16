import { describe, expect, it } from "vitest";

import {
  BUILD_ROLES,
  assemblyName,
  bloomGenomeConfig,
  bloomGenomesNote,
  buildFileUri,
  geneTrackId,
  mergeBrowserConfig,
  notOfferedMessage,
  parseGenomeVersion,
  unavailableReason,
  type BloomGenome,
} from "./bloom-genomes";

const genome: BloomGenome = {
  buildId: 7,
  genome: "athaliana.col-0.hpiv02",
  version: 2,
  assembly: "TAIR10",
  annotation: "Araport11",
  species: "Arabidopsis thaliana",
};

const loc = (uri: string) => ({ uri, locationType: "UriLocation" });

describe("assemblyName / parseGenomeVersion", () => {
  it("names a version <genome>.v<N> and reads it back", () => {
    expect(assemblyName("athaliana.col-0.hpiv02", 2)).toBe("athaliana.col-0.hpiv02.v2");
    expect(parseGenomeVersion("athaliana.col-0.hpiv02.v2")).toEqual({
      genome: "athaliana.col-0.hpiv02",
      version: 2,
    });
  });

  it.each([
    "athaliana",
    "athaliana.v",
    "athaliana.v0",
    "athaliana.vx",
    ".v1",
    "a.v1.5",
    "a.v2147483648",
    "a.v99999999999999999999",
  ])("reads no version from %j", (text) => {
    expect(parseGenomeVersion(text)).toBeNull();
  });

  it("accepts the largest integer version", () => {
    expect(parseGenomeVersion("a.v2147483647")?.version).toBe(2147483647);
  });
});

describe("bloomGenomeConfig", () => {
  const { assembly, track } = bloomGenomeConfig(genome);

  it("labels the assembly like the single-cell form, with the species", () => {
    expect(assembly.name).toBe("athaliana.col-0.hpiv02.v2");
    expect(assembly.displayName).toBe(
      "athaliana.col-0.hpiv02 v2 — TAIR10 / Araport11 (Arabidopsis thaliana)",
    );
  });

  it("reads the sequence through the build's stable file addresses", () => {
    expect(assembly.sequence.adapter).toEqual({
      type: "BgzipFastaAdapter",
      fastaLocation: loc("/app/jbrowse/files/7/fasta"),
      faiLocation: loc("/app/jbrowse/files/7/fasta_fai"),
      gziLocation: loc("/app/jbrowse/files/7/fasta_gzi"),
    });
  });

  it("reads the genes from the build's tabix GFF3, under the shared gene-track ID", () => {
    expect(track.trackId).toBe(geneTrackId("athaliana.col-0.hpiv02.v2"));
    expect(track.assemblyNames).toEqual(["athaliana.col-0.hpiv02.v2"]);
    expect(track.description).toBe("Browser build 7");
    expect(track.adapter).toEqual({
      type: "Gff3TabixAdapter",
      gffGzLocation: loc("/app/jbrowse/files/7/gff3"),
      index: { location: loc("/app/jbrowse/files/7/gff3_tbi"), indexType: "TBI" },
    });
  });

  it("uses every build role exactly once", () => {
    const uris = JSON.stringify({ assembly, track }).match(/\/app\/jbrowse\/files\/7\/[a-z0-9_]+/g);
    expect([...(uris ?? [])].sort()).toEqual(BUILD_ROLES.map((r) => buildFileUri(7, r)).sort());
  });

  it("leaves out what is not recorded", () => {
    const bare = bloomGenomeConfig({ ...genome, assembly: null, annotation: null, species: null });
    expect(bare.assembly.displayName).toBe("athaliana.col-0.hpiv02 v2");
  });
});

describe("mergeBrowserConfig", () => {
  const outside = {
    assemblies: [{ name: "Outside.A" }],
    tracks: [{ trackId: "outside-genes" }],
    configuration: { theme: "x" },
  };

  it("adds Bloom's genomes after the outside ones without changing them", () => {
    const merged = mergeBrowserConfig(outside, [genome]);
    expect(merged.assemblies.map((a) => a.name)).toEqual(["Outside.A", "athaliana.col-0.hpiv02.v2"]);
    expect(merged.tracks).toHaveLength(2);
    expect(merged.configuration).toEqual({ theme: "x" });
    expect(outside.assemblies).toHaveLength(1);
  });

  it("is the outside config when Bloom has nothing browsable", () => {
    expect(mergeBrowserConfig(outside, [])).toEqual(outside);
  });
});

describe("unavailableReason", () => {
  it("is unknown when no such version exists", () => {
    expect(unavailableReason(null)).toBe("unknown");
  });

  it("is not ready while the version is uploading", () => {
    expect(unavailableReason({ status: "uploading", builds: [] })).toBe("not ready");
  });

  it("is abandoned for an abandoned upload", () => {
    expect(unavailableReason({ status: "abandoned", builds: [] })).toBe("abandoned");
  });

  it("is withdrawn for a withdrawn version, whatever its builds", () => {
    expect(unavailableReason({ status: "withdrawn", builds: ["ready"] })).toBe("withdrawn");
  });

  it("blames only the files when a ready version's browser build was withdrawn", () => {
    expect(unavailableReason({ status: "ready", builds: ["withdrawn", "abandoned"] })).toBe(
      "files withdrawn",
    );
  });

  it.each([[[]], [["uploading"]], [["abandoned"]], [["withdrawn", "uploading"]]])(
    "is not yet built for a ready version with builds %j",
    (builds) => {
      expect(unavailableReason({ status: "ready", builds })).toBe("not built");
    },
  );

  it("is null for a ready version with a ready build", () => {
    expect(unavailableReason({ status: "ready", builds: ["withdrawn", "ready"] })).toBeNull();
  });
});

describe("notOfferedMessage", () => {
  it.each([
    ["unknown", 'Bloom has no genome version "g.v9".'],
    ["not ready", "g.v9 has not finished uploading, so it cannot be browsed yet."],
    ["abandoned", "g.v9's upload was abandoned, so it cannot be browsed."],
    ["withdrawn", "g.v9 has been withdrawn, so it cannot be browsed."],
    ["files withdrawn", "g.v9's browser files were withdrawn; it becomes browsable again once they are rebuilt."],
    ["not built", "g.v9 has no browser files yet; it becomes browsable once they are built."],
  ] as const)("says why for %s", (reason, message) => {
    expect(notOfferedMessage("g.v9", reason)).toBe(message);
  });
});

describe("bloomGenomesNote", () => {
  it("counts one version", () => {
    expect(bloomGenomesNote(1, null)).toBe(
      "1 Bloom genome version listed with the reference genomes; a version appears once its browser files are built.",
    );
  });

  it("counts several versions", () => {
    expect(bloomGenomesNote(3, null)).toMatch(/^3 Bloom genome versions listed/);
  });

  it("says plainly when none is browsable", () => {
    expect(bloomGenomesNote(0, null)).toMatch(/^Bloom has no browsable genome versions yet/);
  });

  it("gives the reason Bloom's genomes are missing, and that the outside ones work", () => {
    expect(bloomGenomesNote(0, "Bloom's own genomes could not be read.")).toBe(
      "Bloom's own genomes could not be read. The reference genomes below still work.",
    );
  });
});
