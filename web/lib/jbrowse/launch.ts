import { geneTrackId, sequenceTrackId, type LoadedBrowserConfig } from "./bloom-genomes";
import { densityTrackId } from "./gene-density";
import type { GenePlace } from "./gene-links";
import type { PicksPlan } from "./several-genes";

/** Where a link asks the browser to open: an assembly, and a location (a region, or a gene on
 * the outside assemblies, which have a gene index); no location shows the whole assembly. */
export interface LaunchTarget {
  assembly: string;
  loc?: string;
}

/** What to open: the target, with the gene track shown for a Bloom genome version. */
export interface LaunchPlan extends LaunchTarget {
  tracks: string[];
  /** Open the default views, once the assembly's chromosomes are known. */
  firstRegion?: true;
}

/** The link parameters the browser page reads; a link with none of them opens the default. */
const BROWSER_PARAMS = ["genome", "reference", "gene", "genes", "loc", "missing"];

/** How much of the longest chromosome the default gene view shows, bp. */
export const DEFAULT_SPAN_BP = 50_000;

/** One linear view to open: a region and the tracks shown on it. */
export interface ViewToOpen {
  loc: string;
  tracks: string[];
}

/** Where a link opens: `?genome=<name>.v<N>[&loc=<region>]` for a Bloom genome version, or the
 * genes page's `?reference=<assembly>&gene=<gene>`; null when neither is given. */
export function launchTarget(params: URLSearchParams): LaunchTarget | null {
  const genome = params.get("genome")?.trim();
  if (genome) return { assembly: genome, loc: params.get("loc")?.trim() || undefined };
  const assembly = params.get("reference")?.trim();
  const loc = params.get("gene")?.trim();
  return assembly && loc ? { assembly, loc } : null;
}

/** True when the link has none of the browser page's parameters. */
export function namesNothing(params: URLSearchParams): boolean {
  return BROWSER_PARAMS.every((name) => !params.has(name));
}

/** The target if the browser offers its assembly, else null (the page says why). With no target
 * and `useDefault`, the most recently built Bloom genome version (highest browser build). */
export function launchPlan(
  target: LaunchTarget | null,
  loaded: Pick<LoadedBrowserConfig, "config" | "bloomGenomes" | "bloomBuilds">,
  useDefault = false,
): LaunchPlan | null {
  if (!target) {
    if (!useDefault) return null;
    const newest = [...loaded.bloomGenomes].sort((a, b) => loaded.bloomBuilds[b] - loaded.bloomBuilds[a])[0];
    return newest ? { assembly: newest, tracks: bloomTracks(newest), firstRegion: true } : null;
  }
  if (!loaded.config.assemblies.some((a) => a.name === target.assembly)) return null;
  const tracks = loaded.bloomGenomes.includes(target.assembly) ? bloomTracks(target.assembly) : [];
  return { ...target, tracks };
}

/** The tracks shown with a Bloom genome version: its genes (density is ticked when wanted). */
export function bloomTracks(assembly: string): string[] {
  return [geneTrackId(assembly)];
}

/** A genome's longest chromosome and its length (the first of equal ones, so an organelle listed
 * first is passed over); none when the chromosomes are unknown. */
export function longestChromosome(contigLengths: Record<string, number>): [string, number] | undefined {
  return Object.entries(contigLengths).reduce<[string, number] | undefined>(
    (best, entry) => (!best || entry[1] > best[1] ? entry : best),
    undefined,
  );
}

/** A chromosome's first `DEFAULT_SPAN_BP`, or all of a shorter one. */
export function geneSpan([chromosome, length]: [string, number]): string {
  return `${chromosome}:1..${Math.min(length, DEFAULT_SPAN_BP)}`;
}

/** The default views of a Bloom genome's longest chromosome: the whole chromosome with gene
 * density, then its first `DEFAULT_SPAN_BP` with the reference sequence and genes. */
export function defaultViews(assembly: string, contigLengths: Record<string, number>): ViewToOpen[] | undefined {
  const longest = longestChromosome(contigLengths);
  if (!longest) return undefined;
  return [
    { loc: `${longest[0]}:1..${longest[1]}`, tracks: [densityTrackId(assembly)] },
    { loc: geneSpan(longest), tracks: [sequenceTrackId(assembly), geneTrackId(assembly)] },
  ];
}

/** The picks to open instead of the link's location: those placed on the launched genome. */
export function picksToOpen(plan: LaunchPlan | null, picks: PicksPlan | null): GenePlace[] | null {
  return plan && picks?.assembly === plan.assembly && picks.places.length ? picks.places : null;
}
