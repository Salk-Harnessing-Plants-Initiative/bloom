/** What the gene search needs from JBrowse's session: which genomes are open, and moving one
 * genome's view to genes. Typed loosely, as JBrowse's own session model is untyped here. */

import type { GenePlace } from "./gene-links";
import { picksView } from "./several-genes";

export interface SessionView {
  type: string;
  assemblyNames?: string[];
  /** A new view's opening request, until it has opened. */
  init?: { assembly?: string } | null;
  navToLocString?: (loc: string, assembly?: string) => Promise<void> | void;
  setInit?: (init: { assembly: string; loc: string; highlight: string[] }) => void;
  setHighlight?: (highlight: unknown[]) => void;
}

export interface BrowserSession {
  views: SessionView[];
  addView?: (type: string, snapshot: unknown) => unknown;
  removeView?: (view: SessionView) => void;
  assemblyManager?: {
    waitForAssembly: (name: string) => Promise<{ regions?: { refName: string; end: number }[] } | undefined>;
  };
}

const LINEAR = "LinearGenomeView";

/** The genomes open in the session's linear views, each once, in view order. */
export function openAssemblies(session: Pick<BrowserSession, "views"> | null): string[] {
  const names = (session?.views ?? []).filter((v) => v.type === LINEAR).flatMap((v) => v.assemblyNames ?? []);
  return [...new Set(names)];
}

/** The browser builds a gene search covers: the picked genome's, else the open Bloom genomes',
 * else every Bloom genome the page offers. */
export function searchScope(open: string[], picked: string | null, bloomBuilds: Record<string, number>): number[] {
  const builds = (picked ? [picked] : open).map((a) => bloomBuilds[a]).filter((b) => b !== undefined);
  return builds.length ? builds : Object.values(bloomBuilds);
}

/** The open genomes for the gene search: none (so every genome) while the only open view is the
 * default one the page opened by itself. */
export function searchedGenomes(open: string[], defaultGenome: string | null): string[] {
  return open.length === 1 && open[0] === defaultGenome ? [] : open;
}

/** Adds a linear view of the genome per region, in order, each with its tracks. */
export function openViews(session: BrowserSession, assembly: string, views: { loc: string; tracks: string[] }[]): void {
  for (const { loc, tracks } of views) addView(session, { assembly, loc, tracks });
}

/** Closes every open view, then adds the genome's views. */
export function replaceViews(session: BrowserSession, assembly: string, views: { loc: string; tracks: string[] }[]): void {
  const { removeView } = session;
  if (!removeView || !session.addView) throw new Error(`The browser could not open ${assembly}.`);
  for (const view of [...session.views]) removeView(view);
  openViews(session, assembly, views);
}

/** Adds an empty linear view, which asks for a genome and location. */
export function openEmptyView(session: BrowserSession): void {
  session.addView?.(LINEAR, {});
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

/** The latest linear view of a genome, including one still opening. */
function viewOf(session: BrowserSession, assembly: string): SessionView | undefined {
  return session.views.findLast(
    (v) => v.type === LINEAR && (v.assemblyNames?.[0] === assembly || v.init?.assembly === assembly),
  );
}

function addView(session: BrowserSession, init: Record<string, unknown> & { assembly: string }): void {
  if (!session.addView) throw new Error(`The browser could not open ${init.assembly}.`);
  session.addView(LINEAR, { init });
}

/** Moves the gene's genome's view to the gene, leaving other views; opens one when none is. */
export async function goToGene(session: BrowserSession, place: GenePlace, tracks: string[]): Promise<void> {
  const loc = `${place.contig}:${place.start}..${place.end}`;
  const view = viewOf(session, place.assembly);
  if (view?.navToLocString) {
    await view.navToLocString(loc, place.assembly);
    return;
  }
  addView(session, { assembly: place.assembly, loc, tracks });
}

/** Each contig's length in the browser's copy of a genome; none when it cannot tell. */
export async function contigLengths(session: BrowserSession, assembly: string): Promise<Record<string, number>> {
  const asm = await session.assemblyManager?.waitForAssembly(assembly);
  return Object.fromEntries((asm?.regions ?? []).map((r) => [r.refName, r.end]));
}

/** Opens picked genes of one genome in its view (or a new one), leaving other views. */
export async function openPicks(
  session: BrowserSession,
  assembly: string,
  places: GenePlace[],
  tracks: string[],
): Promise<void> {
  const { loc, highlight } = picksView(places, await contigLengths(session, assembly));
  const view = viewOf(session, assembly);
  if (view?.setInit) {
    view.setHighlight?.([]);
    view.setInit({ assembly, loc, highlight });
    return;
  }
  addView(session, { assembly, loc, highlight, tracks });
}
