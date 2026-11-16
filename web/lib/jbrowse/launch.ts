/** Where a link asks the browser to open: an assembly and a location (a gene or a region). */
export interface LaunchTarget {
  assembly: string;
  loc: string;
}

/** Where a link opens: `?genome=<name>.v<N>&loc=<gene or region>` for a Bloom genome version,
 * or the genes page's `?reference=<assembly>&gene=<gene>`; null when neither pair is complete. */
export function launchTarget(params: URLSearchParams): LaunchTarget | null {
  for (const [assemblyKey, locKey] of [["genome", "loc"], ["reference", "gene"]]) {
    const assembly = params.get(assemblyKey)?.trim();
    const loc = params.get(locKey)?.trim();
    if (assembly && loc) return { assembly, loc };
  }
  return null;
}
