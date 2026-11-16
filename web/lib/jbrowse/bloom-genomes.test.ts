import { describe, expect, it } from "vitest";

import {
  assemblyName,
  bloomGenomeConfig,
  mergeBrowserConfig,
  notOfferedMessage,
  parseGenomeVersion,
  REFRESH_MARGIN_MS,
  refreshDue,
  unavailableReason,
  type BloomGenome,
  type BuildFileUrls,
} from "./bloom-genomes";

const urls: BuildFileUrls = {
  fasta: "https://bloom.example/fa.gz?token=1",
  fasta_fai: "https://bloom.example/fa.gz.fai?token=2",
  fasta_gzi: "https://bloom.example/fa.gz.gzi?token=3",
  gff3: "https://bloom.example/gff3.gz?token=4",
  gff3_tbi: "https://bloom.example/gff3.gz.tbi?token=5",
};

const genome: BloomGenome = {
  genome: "athaliana.col-0.hpiv02",
  version: 2,
  species: "Arabidopsis thaliana",
  folder: "athaliana.col-0.hpiv02/v2/browser/b1",
};

describe("assemblyName / parseGenomeVersion", () => {
  it("names a version <genome>.v<N> and reads it back", () => {
    expect(assemblyName("athaliana.col-0.hpiv02", 2)).toBe("athaliana.col-0.hpiv02.v2");
    expect(parseGenomeVersion("athaliana.col-0.hpiv02.v2")).toEqual({
      genome: "athaliana.col-0.hpiv02",
      version: 2,
    });
  });

  it.each(["athaliana", "athaliana.v", "athaliana.v0", "athaliana.vx", ".v1", "a.v1.5"])(
    "reads no version from %j",
    (text) => {
      expect(parseGenomeVersion(text)).toBeNull();
    },
  );
});

describe("bloomGenomeConfig", () => {
  const { assembly, track } = bloomGenomeConfig(genome, urls);

  it("labels the assembly with genome, version and species", () => {
    expect(assembly.name).toBe("athaliana.col-0.hpiv02.v2");
    expect(assembly.displayName).toBe("athaliana.col-0.hpiv02 v2 (Arabidopsis thaliana)");
  });

  it("reads the sequence from the build's bgzip FASTA and its two indexes", () => {
    expect(assembly.sequence.adapter).toEqual({
      type: "BgzipFastaAdapter",
      fastaLocation: { uri: urls.fasta, locationType: "UriLocation" },
      faiLocation: { uri: urls.fasta_fai, locationType: "UriLocation" },
      gziLocation: { uri: urls.fasta_gzi, locationType: "UriLocation" },
    });
  });

  it("reads the genes from the build's tabix GFF3", () => {
    expect(track.assemblyNames).toEqual(["athaliana.col-0.hpiv02.v2"]);
    expect(track.adapter).toEqual({
      type: "Gff3TabixAdapter",
      gffGzLocation: { uri: urls.gff3, locationType: "UriLocation" },
      index: { location: { uri: urls.gff3_tbi, locationType: "UriLocation" }, indexType: "TBI" },
    });
  });

  it("leaves the species out of the label when there is none", () => {
    expect(bloomGenomeConfig({ ...genome, species: null }, urls).assembly.displayName).toBe(
      "athaliana.col-0.hpiv02 v2",
    );
  });
});

describe("mergeBrowserConfig", () => {
  const outside = {
    assemblies: [{ name: "Outside.A" }],
    tracks: [{ trackId: "outside-genes" }],
    configuration: { theme: "x" },
  };

  it("adds Bloom's genomes after the outside ones without changing them", () => {
    const merged = mergeBrowserConfig(outside, [bloomGenomeConfig(genome, urls)]);
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

  it.each(["uploading", "abandoned"])("is not ready for a %s version", (status) => {
    expect(unavailableReason({ status, builds: [] })).toBe("not ready");
  });

  it("is withdrawn for a withdrawn version, whatever its builds", () => {
    expect(unavailableReason({ status: "withdrawn", builds: ["ready"] })).toBe("withdrawn");
  });

  it("is withdrawn when the version's only finished build was withdrawn", () => {
    expect(unavailableReason({ status: "ready", builds: ["withdrawn", "abandoned"] })).toBe(
      "withdrawn",
    );
  });

  it.each([[[]], [["uploading"]], [["abandoned"]]])(
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
    ["unknown", 'Bloom has no genome version "at.col0.v9".'],
    ["not ready", "at.col0.v9 has not finished uploading, so it cannot be browsed."],
    ["withdrawn", "at.col0.v9 has been withdrawn, so it cannot be browsed."],
    ["not built", "at.col0.v9 has no browser files yet; it becomes browsable once they are built."],
  ] as const)("says why for %s", (reason, message) => {
    expect(notOfferedMessage("at.col0.v9", reason)).toBe(message);
  });
});

describe("refreshDue", () => {
  const expiresAt = 10 * REFRESH_MARGIN_MS;

  it("is due from the margin before expiry onward", () => {
    expect(refreshDue(expiresAt, expiresAt - REFRESH_MARGIN_MS - 1)).toBe(false);
    expect(refreshDue(expiresAt, expiresAt - REFRESH_MARGIN_MS)).toBe(true);
    expect(refreshDue(expiresAt, expiresAt + 1)).toBe(true);
  });

  it("is never due when there are no signed URLs", () => {
    expect(refreshDue(null, Number.MAX_SAFE_INTEGER)).toBe(false);
  });
});
