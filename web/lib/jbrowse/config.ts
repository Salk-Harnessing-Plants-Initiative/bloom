import outsideGenomes from "./outside-genomes.json";

/** A JBrowse configuration, as `createViewState` reads it. */
export interface BrowserConfig {
  assemblies: { name: string }[];
  tracks: unknown[];
  [key: string]: unknown;
}

/** The genome browser's configuration, built on the server: the HPI genomes hosted outside Bloom. */
export function browserConfig(): BrowserConfig {
  return outsideGenomes;
}
