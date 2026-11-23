import { describe, expect, it } from "vitest";

import { bloomGenomeConfig } from "./bloom-genomes";
import { browserConfig } from "./config";
import { BLOOM_FONT, BLOOM_LOGO, BLOOM_THEME, CONTIG_COLOURS, GENE_RENDERER, withBloomLook } from "./theme";

describe("withBloomLook", () => {
  const config = {
    assemblies: [{ name: "A" }, { name: "B", refNameColors: ["red"] }],
    configuration: { other: 1 },
    tracks: [
      { type: "FeatureTrack", trackId: "genes" },
      { type: "FeatureTrack", trackId: "styled", displays: ["own"] },
      { type: "AlignmentsTrack", trackId: "reads" },
    ],
  };

  it("sets Bloom's theme and keeps the other settings", () => {
    expect(withBloomLook(config).configuration).toEqual({ other: 1, theme: BLOOM_THEME, logoPath: BLOOM_LOGO });
  });

  it("draws feature tracks in Bloom's colours, leaving tracks with their own displays", () => {
    const [genes, styled, reads] = withBloomLook(config).tracks as Record<string, unknown>[];
    expect(genes.displays).toEqual([
      { type: "LinearBasicDisplay", displayId: "genes-LinearBasicDisplay", renderer: GENE_RENDERER },
    ]);
    expect(styled.displays).toEqual(["own"]);
    expect(reads).toEqual({ type: "AlignmentsTrack", trackId: "reads" });
  });

  it("colours chromosome names in Bloom's colours, leaving assemblies with their own", () => {
    expect(withBloomLook(config).assemblies).toEqual([
      { name: "A", refNameColors: CONTIG_COLOURS },
      { name: "B", refNameColors: ["red"] },
    ]);
  });

  it("does not change what it is given", () => {
    withBloomLook(config);
    expect(config.tracks[0]).toEqual({ type: "FeatureTrack", trackId: "genes" });
  });
});

describe("the browser's configuration", () => {
  it("uses Bloom's theme and font", () => {
    const theme = (browserConfig().configuration as { theme: typeof BLOOM_THEME }).theme;
    expect(theme.palette.primary.main).toBe("#3f6212");
    expect(theme.typography.fontFamily).toBe(BLOOM_FONT);
  });

  it("has a pale header with Bloom's mark", () => {
    const configuration = browserConfig().configuration as { theme: typeof BLOOM_THEME; logoPath: unknown };
    expect(configuration.theme.components.MuiAppBar.styleOverrides.root.backgroundColor).toBe("#f5f5f4");
    expect(configuration.logoPath).toEqual({ uri: "/logo-mark.png", locationType: "UriLocation" });
  });

  it("fits Bloom's mark inside the header", () => {
    const root = BLOOM_THEME.components.MuiAppBar.styleOverrides.root as Record<string, unknown>;
    expect(root['& img[alt="Custom logo"]']).toEqual({ display: "block", height: 40, width: "auto" });
  });

  it("draws every outside gene track in Bloom's colours", () => {
    const features = (browserConfig().tracks as { type: string; displays?: unknown }[]).filter(
      (t) => t.type === "FeatureTrack",
    );
    expect(features.length).toBeGreaterThan(0);
    for (const track of features) {
      expect(track.displays).toEqual([expect.objectContaining({ renderer: GENE_RENDERER })]);
    }
  });

  it("draws a Bloom genome in Bloom's colours", () => {
    const { assembly, track } = bloomGenomeConfig({
      buildId: 7, genome: "athaliana", version: 2, assembly: null, annotation: null, species: null,
    });
    expect(assembly.refNameColors).toEqual(CONTIG_COLOURS);
    expect(track.displays).toEqual([
      { type: "LinearBasicDisplay", displayId: `${track.trackId}-LinearBasicDisplay`, renderer: GENE_RENDERER },
    ]);
  });
});
