import { geneTrackId, type LoadedBrowserConfig } from "./bloom-genomes";

/** Where a link asks the browser to open: an assembly, and a location (a region, or a gene on
 * the outside assemblies, which have a gene index); no location shows the whole assembly. */
export interface LaunchTarget {
  assembly: string;
  loc?: string;
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
  const tracks = loaded.bloomGenomes.includes(target.assembly) ? [geneTrackId(target.assembly)] : [];
  return { ...target, tracks };
}
