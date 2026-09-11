/**
 * Turning the rows the cell query returns into the typed arrays the map draws.
 *
 * These are pure and live outside the canvas component so they can be tested
 * without a WebGL context, and so the component has one job.
 */

import {
  ORPHAN_CLUSTER_ORDINAL,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

/** Give the UMAP bounding box a small border. */
const NORMALIZE_PADDING = 1.05;

const ORPHAN_GRAY: [number, number, number] = [0.6, 0.6, 0.6];

export function hexToRgb(hex: string | null): [number, number, number] {
  if (!hex) return [0.5, 0.5, 0.5];
  const trimmed = hex.replace(/^#/, "");
  if (trimmed.length !== 6) return [0.5, 0.5, 0.5];
  const n = parseInt(trimmed, 16);
  if (Number.isNaN(n)) return [0.5, 0.5, 0.5];
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

export function packClusterColors(
  cells: CellArraysRow[],
  clusters: Cluster[],
): Float32Array {
  const paletteRgb = new Map<number, [number, number, number]>();
  for (const c of clusters) {
    paletteRgb.set(c.ordinal, hexToRgb(c.color));
  }
  const out = new Float32Array(cells.length * 4);
  for (let i = 0; i < cells.length; i++) {
    const ord = cells[i].cluster_ordinal;
    const rgb =
      ord === ORPHAN_CLUSTER_ORDINAL
        ? ORPHAN_GRAY
        : paletteRgb.get(ord) ?? ORPHAN_GRAY;
    out[i * 4] = rgb[0];
    out[i * 4 + 1] = rgb[1];
    out[i * 4 + 2] = rgb[2];
    out[i * 4 + 3] = 1.0;
  }
  return out;
}

/** The filter row for the cells' sample; every other row is one of their labels. */
export const SAMPLE_FILTER = "sample";

/** Values hidden per filter row: the sample row and each label. */
export type HiddenValues = ReadonlyMap<string, ReadonlySet<string>>;

/** A cell's value for one filter row, or null when it has none. */
export function filterValue(
  cell: Pick<CellArraysRow, "replicate" | "facets">,
  filter: string,
): string | null {
  const value = filter === SAMPLE_FILTER ? cell.replicate : cell.facets?.[filter];
  return value == null || value === "" ? null : value;
}

/** Whether any row other than `skip` hides this cell. A cell with no value for
 *  a row is never hidden by it: the row says nothing about that cell. */
function ruledOut(
  cell: Pick<CellArraysRow, "replicate" | "facets">,
  hidden: HiddenValues,
  skip?: string,
): boolean {
  for (const [filter, values] of hidden) {
    if (filter === skip || values.size === 0) continue;
    const value = filterValue(cell, filter);
    if (value !== null && values.has(value)) return true;
  }
  return false;
}

/** Which cells are drawn: 1 for shown, 0 for hidden.
 *
 * Hiding is done here rather than by filtering the cells, and that is the whole
 * reason positions and axis ranges do not move when a sample is switched off:
 * `packPositions` runs once over every cell and never sees the hidden set.
 */
export function packVisibility(
  cells: Pick<CellArraysRow, "replicate" | "facets">[],
  clusterOrdinals: Uint8Array,
  hiddenClusters: ReadonlySet<number>,
  hidden: HiddenValues = new Map(),
): Float32Array {
  const out = new Float32Array(cells.length);
  for (let i = 0; i < cells.length; i++) {
    out[i] =
      hiddenClusters.has(clusterOrdinals[i]) || ruledOut(cells[i], hidden)
        ? 0
        : 1.0;
  }
  return out;
}

/** Each value of one row, counting only the cells the other rows leave on the
 *  map, in first-seen order. A value no cell can reach shows 0 rather than
 *  disappearing. */
export function countsFor(
  cells: Pick<CellArraysRow, "replicate" | "facets">[],
  hidden: HiddenValues,
  filter: string,
): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const cell of cells) {
    const value = filterValue(cell, filter);
    if (value === null) continue;
    counts.set(value, (counts.get(value) ?? 0) + (ruledOut(cell, hidden, filter) ? 0 : 1));
  }
  return [...counts].map(([name, count]) => ({ name, count }));
}

export function packPositions(cells: CellArraysRow[]): {
  positions: Float32Array;
  normScale: number;
  normCenterX: number;
  normCenterY: number;
} {
  const n = cells.length;
  const positions = new Float32Array(n * 2);
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    positions[i * 2] = cells[i].x;
    positions[i * 2 + 1] = cells[i].y;
    if (cells[i].x < minX) minX = cells[i].x;
    if (cells[i].x > maxX) maxX = cells[i].x;
    if (cells[i].y < minY) minY = cells[i].y;
    if (cells[i].y > maxY) maxY = cells[i].y;
  }
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const rangeX = maxX - minX;
  const rangeY = maxY - minY;
  const range = Math.max(rangeX, rangeY) || 1;
  const scale = 2 / (range * NORMALIZE_PADDING);
  for (let i = 0; i < n; i++) {
    positions[i * 2] = (positions[i * 2] - centerX) * scale;
    positions[i * 2 + 1] = (positions[i * 2 + 1] - centerY) * scale;
  }
  return { positions, normScale: scale, normCenterX: centerX, normCenterY: centerY };
}

export interface CellArrays {
  clusterOrdinals: Uint8Array;
  orphanCount: number;
  /** The filter rows the cells offer: the sample row when any cell records a
   *  sample, then each label in the order first seen. */
  filters: string[];
  /** Per filter row, the cells with no value for it. They stay on the map
   *  whatever that row hides. */
  unlabelled: Record<string, number>;
}

/** One pass over the cells for everything the sidebar and the toggles need. */
export function packCellArrays(cells: CellArraysRow[]): CellArrays {
  const clusterOrdinals = new Uint8Array(cells.length);
  const labelled = new Map<string, number>();
  let orphanCount = 0;
  let withSample = 0;
  for (let i = 0; i < cells.length; i++) {
    clusterOrdinals[i] = cells[i].cluster_ordinal;
    if (cells[i].cluster_ordinal === ORPHAN_CLUSTER_ORDINAL) orphanCount++;
    if (filterValue(cells[i], SAMPLE_FILTER) !== null) withSample++;
    for (const [name, value] of Object.entries(cells[i].facets ?? {})) {
      // A label named like the sample row would be shadowed by it.
      if (name === SAMPLE_FILTER || value == null || value === "") continue;
      labelled.set(name, (labelled.get(name) ?? 0) + 1);
    }
  }
  const filters = [...(withSample > 0 ? [SAMPLE_FILTER] : []), ...labelled.keys()];
  const unlabelled: Record<string, number> = {};
  if (withSample > 0) unlabelled[SAMPLE_FILTER] = cells.length - withSample;
  for (const [name, n] of labelled) unlabelled[name] = cells.length - n;
  return { clusterOrdinals, orphanCount, filters, unlabelled };
}
