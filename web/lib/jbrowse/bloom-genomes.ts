import { genomeVersionLabel } from "@/lib/scrna-jobs";

import type { BrowserConfig } from "./config";
import { densityTrack } from "./gene-density";
import { CONTIG_COLOURS, geneDisplays } from "./theme";

/** A genome version Bloom can show: ready, with the ready browser build `buildId`. */
export interface BloomGenome {
  buildId: number;
  genome: string;
  version: number;
  assembly: string | null;
  annotation: string | null;
  species: string | null;
}

/** The browser's configuration and the assembly names of Bloom's genome versions in it. */
export interface LoadedBrowserConfig {
  config: BrowserConfig;
  bloomGenomes: string[];
  /** Each Bloom genome version's browser build ID, by assembly name. */
  bloomBuilds: Record<string, number>;
  /** A short reason Bloom's genomes are missing; the outside assemblies are still in `config`. */
  error: string | null;
}

/** The five files of a browser build, as named by `genome_browser_build_roles()`. */
export const BUILD_ROLES = ["fasta", "fasta_fai", "fasta_gzi", "gff3", "gff3_tbi"] as const;
export type BuildRole = (typeof BUILD_ROLES)[number];

/** Largest version number a link can name (the column is a Postgres integer). */
const MAX_VERSION = 2_147_483_647;

/** Characters escaped in the gene ID before it goes into the link, `%` first; enough that no ID
 * can leave the link's quotes or its query value. JBrowse's jexl has no encodeURIComponent. */
const LINK_ESCAPES: [string, string][] = [
  ["%", "%25"], ["&", "%26"], ["#", "%23"], ["?", "%3F"], ["+", "%2B"], [" ", "%20"],
  ['"', "%22"], ["'", "%27"], ["<", "%3C"], [">", "%3E"],
];

const escapedGeneId = LINK_ESCAPES.reduce(
  (expr, [from, to]) => `replaceAll(${expr}, ${JSON.stringify(from)}, ${JSON.stringify(to)})`,
  "feature.gene_id",
);

/** A gene's details in the browser gain a link to its orthologs on Bloom's OrthoFinder page.
 * JBrowse evaluates it on a plain copy of the feature, so fields are read directly, not via get(). */
export const ORTHOLOGS_DETAIL =
  `jexl:feature.gene_id ? {Orthologs: '<a href="/app/orthofinder?search=' + ${escapedGeneId} + '">Find orthologs</a>'} : {}`;

/** The stable Bloom address of one build file; it redirects to a freshly signed storage URL. */
export function buildFileUri(buildId: number, role: BuildRole): string {
  return `/app/jbrowse/files/${buildId}/${role}`;
}

const location = (buildId: number, role: BuildRole) => ({
  uri: buildFileUri(buildId, role),
  locationType: "UriLocation",
});

/** The browser's name for a genome version, also the `?genome=` link value. */
export function assemblyName(genome: string, version: number): string {
  return `${genome}.v${version}`;
}

/** The reference sequence track's ID for a genome version's assembly. */
export function sequenceTrackId(assembly: string): string {
  return `${assembly}-sequence`;
}

/** The gene track's ID for a genome version's assembly. */
export function geneTrackId(assembly: string): string {
  return `${assembly}-genes`;
}

/** `<genome>.v<N>` split into its genome and version; null when it is not that shape. */
export function parseGenomeVersion(text: string): { genome: string; version: number } | null {
  const match = /^(.+)\.v([1-9][0-9]*)$/.exec(text);
  if (!match) return null;
  const version = Number(match[2]);
  return version <= MAX_VERSION ? { genome: match[1], version } : null;
}

/** One genome version's assembly (sequence), gene track and gene-density track for JBrowse. */
export function bloomGenomeConfig(g: BloomGenome) {
  const name = assemblyName(g.genome, g.version);
  const label = genomeVersionLabel({ id: g.buildId, ...g });
  return {
    assembly: {
      name,
      displayName: g.species ? `${label} (${g.species})` : label,
      refNameColors: CONTIG_COLOURS,
      sequence: {
        type: "ReferenceSequenceTrack",
        trackId: sequenceTrackId(name),
        adapter: {
          type: "BgzipFastaAdapter",
          fastaLocation: location(g.buildId, "fasta"),
          faiLocation: location(g.buildId, "fasta_fai"),
          gziLocation: location(g.buildId, "fasta_gzi"),
        },
      },
    },
    track: {
      type: "FeatureTrack",
      trackId: geneTrackId(name),
      name: `${g.genome} v${g.version} genes`,
      description: `Browser build ${g.buildId}`,
      formatDetails: { feature: ORTHOLOGS_DETAIL },
      assemblyNames: [name],
      displays: geneDisplays(geneTrackId(name)),
      adapter: {
        type: "Gff3TabixAdapter",
        gffGzLocation: location(g.buildId, "gff3"),
        index: { location: location(g.buildId, "gff3_tbi"), indexType: "TBI" },
      },
    },
    density: densityTrack(name, g.buildId, `${g.genome} v${g.version}`),
  };
}

/** The outside assemblies followed by Bloom's genomes. */
export function mergeBrowserConfig(outside: BrowserConfig, genomes: BloomGenome[]): BrowserConfig {
  if (genomes.length === 0) return outside;
  const bloom = genomes.map(bloomGenomeConfig);
  return {
    ...outside,
    assemblies: [...outside.assemblies, ...bloom.map((b) => b.assembly)],
    tracks: [...outside.tracks, ...bloom.flatMap((b) => [b.track, b.density])],
  };
}

export type UnavailableReason =
  | "unknown"
  | "not ready"
  | "abandoned"
  | "withdrawn"
  | "files withdrawn"
  | "not built";

/** Why a linked genome version is not offered, from its status and its builds' statuses;
 * null when it is offered. */
export function unavailableReason(
  version: { status: string; builds: string[] } | null,
): UnavailableReason | null {
  if (!version) return "unknown";
  if (version.status === "withdrawn") return "withdrawn";
  if (version.status === "abandoned") return "abandoned";
  if (version.status !== "ready") return "not ready";
  if (version.builds.includes("ready")) return null;
  if (version.builds.includes("uploading")) return "not built";
  return version.builds.includes("withdrawn") ? "files withdrawn" : "not built";
}

/** What the page says about a `?genome=` link whose version is not offered. */
export function notOfferedMessage(link: string, reason: UnavailableReason): string {
  switch (reason) {
    case "unknown":
      return `Bloom has no genome version "${link}".`;
    case "not ready":
      return `${link} has not finished uploading, so it cannot be browsed yet.`;
    case "abandoned":
      return `${link}'s upload was abandoned, so it cannot be browsed.`;
    case "withdrawn":
      return `${link} has been withdrawn, so it cannot be browsed.`;
    case "files withdrawn":
      return `${link}'s browser files were withdrawn; it becomes browsable again once they are rebuilt.`;
    case "not built":
      return `${link} has no browser files yet; it becomes browsable once they are built.`;
  }
}

/** The page's line about a `?genome=` link that does not name a version. */
export const INVALID_LINK_MESSAGE =
  "This genome link does not name a genome version; it should end in .v1, .v2 and so on.";

/** The lines above the browser: Bloom's genomes not loaded, then a link's problems; none when
 * all is well. */
export function pageNotes(loadError: string | null, ...notices: (string | null | undefined)[]): string[] {
  return [
    ...(loadError ? [`${loadError} The reference genomes below still work.`] : []),
    ...notices.filter((n): n is string => Boolean(n)),
  ];
}
