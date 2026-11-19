/** Where a link asks the browser to open: an assembly and a location (a gene or a region). */
export interface LaunchTarget {
  assembly: string;
  loc: string;
}

/** The `?reference=<assembly>&gene=<gene>` link from the genes page; null when either is missing. */
export function launchTarget(params: URLSearchParams): LaunchTarget | null {
  const assembly = params.get("reference")?.trim();
  const loc = params.get("gene")?.trim();
  return assembly && loc ? { assembly, loc } : null;
}
