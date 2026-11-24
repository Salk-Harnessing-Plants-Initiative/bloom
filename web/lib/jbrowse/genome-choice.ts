/** The genome selector beside the gene search: which genomes it offers, and the views each opens. */

import { OUTSIDE_GENE_TRACK, type LoadedBrowserConfig } from "./bloom-genomes";
import { defaultViews, geneSpan, longestChromosome, type ViewToOpen } from "./launch";

/** A genome the selector offers. */
export interface GenomeChoice {
  name: string;
  label: string;
}

type Assembly = { name: string; displayName?: string };
type Track = { type?: string; trackId?: string; assemblyNames?: string[] };
type Loaded = Pick<LoadedBrowserConfig, "bloomGenomes"> & { config: { assemblies: unknown[]; tracks: unknown[] } };

/** Bloom's genome versions in the page's order, then the outside assemblies by name. */
export function genomeChoices(loaded: Loaded): { bloom: GenomeChoice[]; outside: GenomeChoice[] } {
  const assemblies = loaded.config.assemblies as Assembly[];
  const choice = (a: Assembly) => ({ name: a.name, label: a.displayName ?? a.name });
  const byName = new Map(assemblies.map((a) => [a.name, a]));
  return {
    bloom: loaded.bloomGenomes.flatMap((name) => (byName.has(name) ? [choice(byName.get(name)!)] : [])),
    outside: assemblies
      .filter((a) => !loaded.bloomGenomes.includes(a.name))
      .map(choice)
      .sort((a, b) => a.name.localeCompare(b.name)),
  };
}

/** An outside assembly's gene tracks. */
export function geneTracksOf(config: { tracks: unknown[] }, assembly: string): string[] {
  return (config.tracks as Track[])
    .filter((t) => t.type === "FeatureTrack" && t.assemblyNames?.includes(assembly) && OUTSIDE_GENE_TRACK.test(t.trackId ?? ""))
    .map((t) => t.trackId!);
}

/** The views a chosen genome opens: a Bloom genome's two default views, or one view of an outside
 * assembly's longest chromosome's first 50 kb with its genes; none when its chromosomes are unknown. */
export function viewsForGenome(
  loaded: Loaded,
  assembly: string,
  contigLengths: Record<string, number>,
): ViewToOpen[] | undefined {
  if (loaded.bloomGenomes.includes(assembly)) return defaultViews(assembly, contigLengths);
  const longest = longestChromosome(contigLengths);
  return longest && [{ loc: geneSpan(longest), tracks: geneTracksOf(loaded.config, assembly) }];
}
