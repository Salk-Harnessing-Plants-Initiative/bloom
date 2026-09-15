/** Counting the cells that carry the transgene, for the maps to show. */

/** The label that records whether a cell carries the transgene, and its value when it does. */
export const TRANSGENE_FACET = "transgene_pos";
export const TRANSGENE_POSITIVE = "True";

export interface TransgeneCount {
  positive: number;
  total: number;
}

/** Per cluster ordinal, its transgene-positive cells out of all its cells;
 *  null when no cell records a transgene status. */
export function transgeneByCluster(
  cells: readonly { cluster_ordinal: number; facets?: Record<string, string> | null }[],
): Map<number, TransgeneCount> | null {
  let recorded = false;
  const out = new Map<number, TransgeneCount>();
  for (const cell of cells) {
    const entry = out.get(cell.cluster_ordinal) ?? { positive: 0, total: 0 };
    entry.total++;
    const status = cell.facets?.[TRANSGENE_FACET];
    if (status != null && status !== "") recorded = true;
    if (status === TRANSGENE_POSITIVE) entry.positive++;
    out.set(cell.cluster_ordinal, entry);
  }
  return recorded ? out : null;
}

/** "82 transgene+" for a group with any, null for one with none. */
export function transgeneBadge(positive: number): string | null {
  return positive > 0 ? `${positive} transgene+` : null;
}

/** The groups with the most transgene-positive cells, most first. */
export function topGroups<T extends { name: string; positive: number }>(groups: readonly T[], k = 3): T[] {
  return groups
    .filter((g) => g.positive > 0)
    .sort((a, b) => b.positive - a.positive || a.name.localeCompare(b.name))
    .slice(0, k);
}
