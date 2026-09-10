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

/** Which cells are drawn: 1 for shown, 0 for hidden.
 *
 * Hiding is done here rather than by filtering the cells, and that is the whole
 * reason positions and axis ranges do not move when a sample is switched off:
 * `packPositions` runs once over every cell and never sees the hidden set.
 */
export function packVisibility(
  cells: Pick<CellArraysRow, "replicate">[],
  clusterOrdinals: Uint8Array,
  hiddenClusters: ReadonlySet<number>,
  hiddenSamples: ReadonlySet<string>,
): Float32Array {
  const out = new Float32Array(cells.length);
  for (let i = 0; i < cells.length; i++) {
    const sample = cells[i].replicate;
    out[i] =
      hiddenClusters.has(clusterOrdinals[i]) ||
      (sample != null && hiddenSamples.has(sample))
        ? 0
        : 1.0;
  }
  return out;
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
  /** Cells per sample, in first-seen order. Cells recording no sample are
   *  counted in `unlabelledCount` instead, because a sample with no name is
   *  not something the map can offer a toggle for. */
  samples: { name: string; count: number }[];
  unlabelledCount: number;
}

/** One pass over the cells for everything the sidebar and the toggles need. */
export function packCellArrays(cells: CellArraysRow[]): CellArrays {
  const clusterOrdinals = new Uint8Array(cells.length);
  const sampleCounts = new Map<string, number>();
  let orphanCount = 0;
  let unlabelledCount = 0;
  for (let i = 0; i < cells.length; i++) {
    clusterOrdinals[i] = cells[i].cluster_ordinal;
    if (cells[i].cluster_ordinal === ORPHAN_CLUSTER_ORDINAL) orphanCount++;
    const sample = cells[i].replicate;
    if (sample != null && sample !== "") {
      sampleCounts.set(sample, (sampleCounts.get(sample) ?? 0) + 1);
    } else {
      unlabelledCount++;
    }
  }
  return {
    clusterOrdinals,
    orphanCount,
    samples: [...sampleCounts].map(([name, count]) => ({ name, count })),
    unlabelledCount,
  };
}
