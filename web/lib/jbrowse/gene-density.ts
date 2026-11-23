/** Gene density: how many genes start in each window of each contig, as a bedGraph track. */

/** The window genes are counted in, bp. */
export const DENSITY_WINDOW_BP = 100_000;

/** Tailwind lime-600, the density bars' colour: about 3:1 on white, lighter than the axis text. */
const DENSITY_COLOUR = "#65a30d";

/** A gene's contig and 1-based start. */
export interface GeneStart {
  contig: string;
  start: number;
}

/** bedGraph lines (zero-based, half-open) counting genes by the window they start in: each contig
 * in natural order, every window from the first to the last gene's, empty ones as 0. */
export function densityBedGraph(genes: GeneStart[]): string {
  const counts = new Map<string, number[]>();
  for (const { contig, start } of genes) {
    const window = Math.floor((start - 1) / DENSITY_WINDOW_BP);
    const row = counts.get(contig) ?? [];
    row[window] = (row[window] ?? 0) + 1;
    counts.set(contig, row);
  }
  const contigs = [...counts.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const lines = contigs.flatMap((contig) =>
    Array.from(counts.get(contig)!, (n, i) => `${contig}\t${i * DENSITY_WINDOW_BP}\t${(i + 1) * DENSITY_WINDOW_BP}\t${n ?? 0}`),
  );
  return lines.length ? `${lines.join("\n")}\n` : "";
}

/** The density track's ID for a genome version's assembly. */
export function densityTrackId(assembly: string): string {
  return `${assembly}-gene-density`;
}

/** The density route's address for a browser build. */
export function densityUri(buildId: number): string {
  return `/app/jbrowse/density/${buildId}`;
}

/** A genome version's gene-density track, read from Bloom's density route. */
export function densityTrack(assembly: string, buildId: number, label: string) {
  const trackId = densityTrackId(assembly);
  return {
    type: "QuantitativeTrack",
    trackId,
    name: `${label} gene density (genes per ${DENSITY_WINDOW_BP / 1000} kb)`,
    assemblyNames: [assembly],
    adapter: { type: "BedGraphAdapter", bedGraphLocation: { uri: densityUri(buildId), locationType: "UriLocation" } },
    displays: [
      {
        type: "LinearWiggleDisplay",
        displayId: `${trackId}-LinearWiggleDisplay`,
        renderers: { XYPlotRenderer: { type: "XYPlotRenderer", color: DENSITY_COLOUR } },
      },
    ],
  };
}
