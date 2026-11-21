/** Several genes of one genome version opened together: which, and how they are laid out. */

import type { GenePlace } from "./gene-links";

/** Most genes opened together. */
export const MAX_PICKS = 20;
/** Genes on one contig this close to the one before (bp) share a region. */
export const JOIN_DISTANCE_BP = 1_000_000;
/** Padding added to each side of a region, as a share of its length. */
export const PADDING_SHARE = 0.1;

/** Smallest region, as a share of all regions' length, so none is too thin to see. */
export const MIN_REGION_SHARE = 0.02;

/** The genes a `?genes=` link placed in one genome version, and the page's lines about picks it
 * could not open. */
export interface PicksPlan {
  assembly: string;
  places: GenePlace[];
  notes: string[];
}

/** A stretch of one contig to show (1-based, both ends included) and the genes in it. */
export interface Region {
  contig: string;
  start: number;
  end: number;
  genes: string[];
}

/** The genes a `?genes=` parameter names, in order without repeats; none, with the count, when
 * there are more than MAX_PICKS. */
export function readPicks(param: string | null | undefined): { genes: string[]; tooMany: number | null } {
  const genes = [...new Set((param ?? "").split(",").map((g) => g.trim()).filter(Boolean))];
  return genes.length > MAX_PICKS ? { genes: [], tooMany: genes.length } : { genes, tooMany: null };
}

/** The genes' regions: genes within JOIN_DISTANCE_BP of the one before joined, padded by
 * PADDING_SHARE, widened to MIN_REGION_SHARE, kept on the contig, then merged where they meet. */
export function groupRegions(places: GenePlace[], contigLengths: Record<string, number> = {}): Region[] {
  const sorted = [...places].sort(
    (a, b) => a.contig.localeCompare(b.contig, undefined, { numeric: true }) || a.start - b.start,
  );
  const joined: Region[] = [];
  for (const place of sorted) {
    const last = joined.at(-1);
    if (last && last.contig === place.contig && place.start - last.end <= JOIN_DISTANCE_BP) {
      last.end = Math.max(last.end, place.end);
      last.genes.push(place.gene);
    } else {
      joined.push({ contig: place.contig, start: place.start, end: place.end, genes: [place.gene] });
    }
  }
  const padded = joined.map((r) => {
    const pad = Math.round((r.end - r.start + 1) * PADDING_SHARE);
    return { ...r, start: r.start - pad, end: r.end + pad };
  });
  const minWidth = Math.ceil(padded.reduce((sum, r) => sum + r.end - r.start + 1, 0) * MIN_REGION_SHARE);
  const merged: Region[] = [];
  for (const r of padded) {
    const extra = Math.max(0, minWidth - (r.end - r.start + 1));
    const length = contigLengths[r.contig];
    const start = Math.max(1, r.start - Math.floor(extra / 2));
    const end = Math.min(length ?? Infinity, r.end + Math.ceil(extra / 2));
    const last = merged.at(-1);
    if (last && last.contig === r.contig && start <= last.end + 1) {
      last.end = Math.max(last.end, end);
      last.genes.push(...r.genes);
    } else {
      merged.push({ ...r, start, end });
    }
  }
  return merged;
}

/** The regions as one JBrowse location: several are shown side by side. */
export function lociText(regions: Region[]): string {
  return regions.map((r) => `${r.contig}:${r.start}..${r.end}`).join(" ");
}

/** A JBrowse highlight for each gene, labelled with its name (zero-based start). */
export function highlightsFor(places: GenePlace[]): string[] {
  return places.map((p) =>
    JSON.stringify({ refName: p.contig, start: p.start - 1, end: p.end, assemblyName: p.assembly, label: p.name || p.gene }),
  );
}

/** How JBrowse shows the picks: their regions as one location, and a highlight per gene. */
export function picksView(places: GenePlace[], contigLengths: Record<string, number>): { loc: string; highlight: string[] } {
  return { loc: lociText(groupRegions(places, contigLengths)), highlight: highlightsFor(places) };
}

/** The page's line naming picked genes that the genome version does not hold. */
export function missingNote(missing: string[], genome: string): string {
  const one = missing.length === 1;
  return `${missing.join(", ")} ${one ? "is" : "are"} not among ${genome}'s genes, so ${one ? "it was" : "they were"} left out.`;
}

/** The page's line for a link naming more genes than MAX_PICKS. */
export function tooManyNote(count: number): string {
  return `Open at most ${MAX_PICKS} genes at once; this link has ${count}.`;
}

/** The browser opened on a genome version with several genes. */
export function picksLink(assembly: string, genes: string[]): string {
  return `/app/jbrowse?genome=${encodeURIComponent(assembly)}&genes=${encodeURIComponent(genes.join(","))}`;
}
