/** What the gene search needs from JBrowse's session: which genomes are open, and moving one
 * genome's view to a gene. Typed loosely, as JBrowse's own session model is untyped here. */

import type { GenePlace } from "./gene-links";

export interface SessionView {
  type: string;
  assemblyNames?: string[];
  navToLocString?: (loc: string, assembly?: string) => Promise<void> | void;
}

export interface BrowserSession {
  views: SessionView[];
  addView?: (type: string, snapshot: unknown) => unknown;
}

const LINEAR = "LinearGenomeView";

/** The genomes open in the session's linear views, each once, in view order. */
export function openAssemblies(session: Pick<BrowserSession, "views"> | null): string[] {
  const names = (session?.views ?? []).filter((v) => v.type === LINEAR).flatMap((v) => v.assemblyNames ?? []);
  return [...new Set(names)];
}

/** The browser builds a gene search covers: the picked genome's, else the open Bloom genomes';
 * null (every Bloom genome) when no Bloom genome is open. */
export function searchScope(
  open: string[],
  picked: string | null,
  bloomBuilds: Record<string, number>,
): number[] | null {
  const builds = (picked ? [picked] : open).map((a) => bloomBuilds[a]).filter((b) => b !== undefined);
  return builds.length ? builds : null;
}

/** Matches grouped by genome version, in the order they came. */
export function groupByGenome(places: GenePlace[]): { assembly: string; label: string; places: GenePlace[] }[] {
  const groups = new Map<string, { assembly: string; label: string; places: GenePlace[] }>();
  for (const p of places) {
    const group = groups.get(p.assembly) ?? { assembly: p.assembly, label: p.label, places: [] };
    group.places.push(p);
    groups.set(p.assembly, group);
  }
  return [...groups.values()];
}

/** Moves the open view of the gene's genome to the gene, leaving other views where they are;
 * opens a view of that genome, with its genes shown, when none is open. */
export async function goToGene(session: BrowserSession, place: GenePlace, geneTrack: string): Promise<void> {
  const loc = `${place.contig}:${place.start}..${place.end}`;
  const view = session.views.find((v) => v.type === LINEAR && v.assemblyNames?.[0] === place.assembly);
  if (view?.navToLocString) {
    await view.navToLocString(loc, place.assembly);
    return;
  }
  session.addView?.(LINEAR, { init: { assembly: place.assembly, loc, tracks: [geneTrack] } });
}
