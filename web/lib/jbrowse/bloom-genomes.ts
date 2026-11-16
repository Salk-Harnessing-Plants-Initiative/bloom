import type { BrowserConfig } from "./config";

/** A genome version Bloom can show: ready, with a ready browser build in `folder`. */
export interface BloomGenome {
  genome: string;
  version: number;
  species: string | null;
  folder: string;
}

/** The five browser files of a build, by role, as URLs the browser can read. */
export interface BuildFileUrls {
  fasta: string;
  fasta_fai: string;
  fasta_gzi: string;
  gff3: string;
  gff3_tbi: string;
}

export const BUILD_ROLES = ["fasta", "fasta_fai", "fasta_gzi", "gff3", "gff3_tbi"] as const;

const uri = (url: string) => ({ uri: url, locationType: "UriLocation" });

/** The browser's name for a genome version, also the `?genome=` link value. */
export function assemblyName(genome: string, version: number): string {
  return `${genome}.v${version}`;
}

/** `<genome>.v<N>` split into its genome and version; null when it is not that shape. */
export function parseGenomeVersion(text: string): { genome: string; version: number } | null {
  const match = /^(.+)\.v([1-9][0-9]*)$/.exec(text);
  return match ? { genome: match[1], version: Number(match[2]) } : null;
}

/** One genome version's assembly (sequence) and gene track for JBrowse. */
export function bloomGenomeConfig(g: BloomGenome, urls: BuildFileUrls) {
  const name = assemblyName(g.genome, g.version);
  const label = `${g.genome} v${g.version}`;
  return {
    assembly: {
      name,
      displayName: g.species ? `${label} (${g.species})` : label,
      sequence: {
        type: "ReferenceSequenceTrack",
        trackId: `${name}-sequence`,
        adapter: {
          type: "BgzipFastaAdapter",
          fastaLocation: uri(urls.fasta),
          faiLocation: uri(urls.fasta_fai),
          gziLocation: uri(urls.fasta_gzi),
        },
      },
    },
    track: {
      type: "FeatureTrack",
      trackId: `${name}-genes`,
      name: `${label} genes`,
      assemblyNames: [name],
      adapter: {
        type: "Gff3TabixAdapter",
        gffGzLocation: uri(urls.gff3),
        index: { location: uri(urls.gff3_tbi), indexType: "TBI" },
      },
    },
  };
}

/** The outside assemblies followed by Bloom's genomes. */
export function mergeBrowserConfig(
  outside: BrowserConfig,
  bloom: ReturnType<typeof bloomGenomeConfig>[],
): BrowserConfig {
  if (bloom.length === 0) return outside;
  return {
    ...outside,
    assemblies: [...outside.assemblies, ...bloom.map((b) => b.assembly)],
    tracks: [...outside.tracks, ...bloom.map((b) => b.track)],
  };
}

export type UnavailableReason = "unknown" | "not ready" | "withdrawn" | "not built";

/** Why a linked genome version is not offered, from its status and its builds' statuses;
 * null when it is offered. */
export function unavailableReason(
  version: { status: string; builds: string[] } | null,
): UnavailableReason | null {
  if (!version) return "unknown";
  if (version.status === "withdrawn") return "withdrawn";
  if (version.status !== "ready") return "not ready";
  if (version.builds.includes("ready")) return null;
  return version.builds.includes("withdrawn") ? "withdrawn" : "not built";
}

/** What the page says about a `?genome=` link whose version is not offered. */
export function notOfferedMessage(link: string, reason: UnavailableReason): string {
  switch (reason) {
    case "unknown":
      return `Bloom has no genome version "${link}".`;
    case "not ready":
      return `${link} has not finished uploading, so it cannot be browsed.`;
    case "withdrawn":
      return `${link} has been withdrawn, so it cannot be browsed.`;
    case "not built":
      return `${link} has no browser files yet; it becomes browsable once they are built.`;
  }
}

/** How long before the signed URLs expire the page fetches fresh ones. */
export const REFRESH_MARGIN_MS = 5 * 60 * 1000;

/** Whether the page should fetch fresh signed URLs now. */
export function refreshDue(expiresAt: number | null, now: number): boolean {
  return expiresAt !== null && now >= expiresAt - REFRESH_MARGIN_MS;
}
