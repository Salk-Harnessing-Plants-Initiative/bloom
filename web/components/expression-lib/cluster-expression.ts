/** A gene's expression per cluster for the genome browser's gene panel, computed with the dataset
 * view's own grouping and stats, so the numbers agree with it and with bloommcp. */

import { cellTypes, groupCells, groupStats } from "./gene-stats";

export interface ClusterRow {
  /** The cluster's ordinal, or null for cells in no known cluster (always last). */
  ordinal: number | null;
  cluster_id: string | null;
  /** The predicted cell-type name, when there is one. */
  name: string | null;
  cells: number;
  expressing: number;
  share: number;
  mean: number;
}

type Cluster = { ordinal: number; cluster_id: string; name: string | null };

/** One row per non-empty cluster in ordinal order, never merged by name, then cells in no known
 * cluster; mean over all of a group's cells, expressing meaning above 0. */
export function clusterExpression(
  values: ArrayLike<number>,
  cellOrdinals: readonly number[],
  clusters: readonly Cluster[],
): ClusterRow[] {
  if (values.length !== cellOrdinals.length) {
    throw new Error(`The gene has ${values.length} values for the dataset's ${cellOrdinals.length} cells.`);
  }
  const types = cellTypes(clusters.map((c) => ({ ...c, color: null })));
  const byOrdinal = new Map(clusters.map((c) => [c.ordinal, c]));
  const groups = groupCells(
    cellOrdinals.map((cluster_ordinal) => ({ cluster_ordinal })),
    types,
    "none",
  );
  return groups.map((group) => {
    const stats = groupStats(values, group.cells)!;
    // groupCells groups by ordinal and drops empty groups; names repeat, so read the cluster from a cell.
    const cluster = byOrdinal.get(cellOrdinals[group.cells[0]]);
    return {
      ordinal: cluster?.ordinal ?? null,
      cluster_id: cluster?.cluster_id ?? null,
      name: cluster?.name || null,
      cells: stats.n,
      expressing: stats.expressing,
      share: stats.share,
      mean: stats.mean,
    };
  });
}
