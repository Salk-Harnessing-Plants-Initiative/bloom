import { geneTrackId, type LoadedBrowserConfig } from "./bloom-genomes";
import { densityTrackId } from "./gene-density";
import type { PicksPlan } from "./several-genes";

/** Where a link asks the browser to open: an assembly, and a location (a region, or a gene on
 * the outside assemblies, which have a gene index); no location shows the whole assembly. */
export interface LaunchTarget {
  assembly: string;
  loc?: string;
  /** JBrowse highlights, one per picked gene. */
  highlight?: string[];
}

/** What to open: the target, with the gene track shown for a Bloom genome version. */
export interface LaunchPlan extends LaunchTarget {
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

/** The target if the browser offers its assembly, else null (the page says why). */
export function launchPlan(
  target: LaunchTarget | null,
  loaded: Pick<LoadedBrowserConfig, "config" | "bloomGenomes">,
): LaunchPlan | null {
  if (!target || !loaded.config.assemblies.some((a) => a.name === target.assembly)) return null;
  const tracks = loaded.bloomGenomes.includes(target.assembly)
    ? [geneTrackId(target.assembly), densityTrackId(target.assembly)]
    : [];
  return { ...target, tracks };
}

/** The target opened on several picked genes: their regions, or the whole genome when none
 * could be placed, with each gene highlighted. */
export function withPicks(target: LaunchTarget | null, picks: PicksPlan | null): LaunchTarget | null {
  if (!target || !picks) return target;
  return { assembly: target.assembly, loc: picks.loc ?? undefined, highlight: picks.highlight };
}
