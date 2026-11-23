import { geneTrackId, type LoadedBrowserConfig } from "./bloom-genomes";
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
  /** Open at the first chromosome's start, once the assembly's chromosomes are known. */
  firstRegion?: true;
}

/** How much of the first chromosome the default view shows, bp. */
export const DEFAULT_SPAN_BP = 1_000_000;

/** Where a link opens: `?genome=<name>.v<N>[&loc=<region>]` for a Bloom genome version, or the
 * genes page's `?reference=<assembly>&gene=<gene>`; null when neither is given. */
export function launchTarget(params: URLSearchParams): LaunchTarget | null {
  const genome = params.get("genome")?.trim();
  if (genome) return { assembly: genome, loc: params.get("loc")?.trim() || undefined };
  const assembly = params.get("reference")?.trim();
  const loc = params.get("gene")?.trim();
  return assembly && loc ? { assembly, loc } : null;
}

/** The target if the browser offers its assembly, else null (the page says why). With nothing
 * named, Bloom's newest genome version (highest browser build) at its first chromosome. */
export function launchPlan(
  target: LaunchTarget | null,
  loaded: Pick<LoadedBrowserConfig, "config" | "bloomGenomes" | "bloomBuilds">,
): LaunchPlan | null {
  if (!target) {
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

/** The first chromosome's first `DEFAULT_SPAN_BP`, in the assembly's own order. */
export function firstRegion(contigLengths: Record<string, number>): string | undefined {
  const [first] = Object.entries(contigLengths);
  return first && `${first[0]}:1..${Math.min(first[1], DEFAULT_SPAN_BP)}`;
}

/** The picks to open instead of the link's location: those placed on the launched genome. */
export function picksToOpen(plan: LaunchPlan | null, picks: PicksPlan | null): GenePlace[] | null {
  return plan && picks?.assembly === plan.assembly && picks.places.length ? picks.places : null;
}
