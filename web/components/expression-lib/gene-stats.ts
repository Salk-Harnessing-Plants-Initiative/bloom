/**
 * A gene's expression in each cell type, and in each genotype or transgene
 * status within it. Every cell of a group counts, zeros included: a cell the
 * gene's file does not name has the value 0, and leaving those out would make
 * a sparse gene look far more expressed than it is.
 */

import type { ClusterMarkers } from "@/components/expression-lib/cluster-markers";
import { TRANSGENE_FACET, TRANSGENE_POSITIVE } from "@/components/expression-lib/transgene";

/** The most genes shown at once. */
export const MAX_GENES = 15;
/** The most genes the view opens on from the stored markers, and from the DE results. */
export const MAX_MARKER_GENES = 15;
export const MAX_DE_GENES = 12;
/** How many of each cell type's markers the view opens on: its first, then its second. */
const MARKER_RANKS = 2;

export const NO_CELL_TYPE = "No cell type";
export const NOT_RECORDED = "Not recorded";

/** The violin is drawn from this many points along the value axis, from values
 *  first counted into this many bins, so its cost does not grow with the cells. */
const VIOLIN_STEPS = 48;
const VIOLIN_BINS = 256;

export type Split = "none" | "genotype" | "transgene";

export interface StatsCell {
  cluster_ordinal: number;
  genotype?: string | null;
  facets?: Record<string, string> | null;
}

export interface CellType {
  ordinal: number;
  name: string;
  color: string | null;
}

/** The cells of one cell type, or of one genotype or transgene status within it. */
export interface CellGroup {
  /** Unique: the cell type's name, or "name · part" when split. */
  key: string;
  cellType: string;
  color: string | null;
  /** The genotype or transgene status, or null when not split. */
  part: string | null;
  /** Indexes into the dataset's cells. */
  cells: Int32Array;
}

export interface GroupStats {
  n: number;
  expressing: number;
  share: number;
  mean: number;
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  lowWhisker: number;
  highWhisker: number;
}

/** The map's cell types: by ordinal, named, or by id where unnamed. */
export function cellTypes(
  clusters: readonly { ordinal: number; cluster_id: string; name: string | null; color: string | null }[],
): CellType[] {
  return [...clusters]
    .sort((a, b) => a.ordinal - b.ordinal)
    .map((c) => ({ ordinal: c.ordinal, name: c.name || c.cluster_id, color: c.color }));
}

function partOf(cell: StatsCell, split: Split): string | null {
  if (split === "genotype") return cell.genotype || null;
  if (split === "transgene") {
    const status = cell.facets?.[TRANSGENE_FACET];
    if (status === TRANSGENE_POSITIVE) return "Transgene+";
    if (status === "False") return "Transgene−";
    return status || null;
  }
  return null;
}

/** Which splits the cells can be put through: only those some cell records. */
export function splitsOffered(cells: readonly StatsCell[]): { genotype: boolean; transgene: boolean } {
  let genotype = false;
  let transgene = false;
  for (const cell of cells) {
    if (cell.genotype) genotype = true;
    if (cell.facets?.[TRANSGENE_FACET]) transgene = true;
    if (genotype && transgene) break;
  }
  return { genotype, transgene };
}

/** The cells by cell type in the map's order, "No cell type" last, each split
 *  into its parts when asked, "Not recorded" last within it. */
export function groupCells(cells: readonly StatsCell[], types: readonly CellType[], split: Split): CellGroup[] {
  const known = new Set(types.map((t) => t.ordinal));
  const byType = new Map<number, number[]>();
  const orphans: number[] = [];
  cells.forEach((cell, i) => {
    if (!known.has(cell.cluster_ordinal)) {
      orphans.push(i);
      return;
    }
    const list = byType.get(cell.cluster_ordinal) ?? [];
    list.push(i);
    byType.set(cell.cluster_ordinal, list);
  });

  const out: CellGroup[] = [];
  const add = (name: string, color: string | null, members: number[]) => {
    if (members.length === 0) return;
    if (split === "none") {
      out.push({ key: name, cellType: name, color, part: null, cells: Int32Array.from(members) });
      return;
    }
    const parts = new Map<string, number[]>();
    for (const i of members) {
      const part = partOf(cells[i], split) ?? NOT_RECORDED;
      const list = parts.get(part) ?? [];
      list.push(i);
      parts.set(part, list);
    }
    const names = [...parts.keys()].sort(
      (a, b) => Number(a === NOT_RECORDED) - Number(b === NOT_RECORDED) || a.localeCompare(b),
    );
    for (const part of names) {
      out.push({ key: `${name} · ${part}`, cellType: name, color, part, cells: Int32Array.from(parts.get(part)!) });
    }
  };
  for (const type of types) add(type.name, type.color, byType.get(type.ordinal) ?? []);
  add(NO_CELL_TYPE, null, orphans);
  return out;
}

/** The group's cell count, cells expressing (above 0), share expressing, mean,
 *  quartiles and whiskers, over all of its cells; null for a group with none. */
export function groupStats(values: ArrayLike<number>, cells: ArrayLike<number>): GroupStats | null {
  const n = cells.length;
  if (n === 0) return null;
  const sorted = new Float64Array(n);
  let sum = 0;
  let expressing = 0;
  for (let k = 0; k < n; k++) {
    const v = values[cells[k]];
    sorted[k] = v;
    sum += v;
    if (v > 0) expressing++;
  }
  sorted.sort();
  const quantile = (p: number) => {
    const h = (n - 1) * p;
    const lo = Math.floor(h);
    const hi = Math.min(n - 1, lo + 1);
    return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
  };
  const q1 = quantile(0.25);
  const median = quantile(0.5);
  const q3 = quantile(0.75);
  const iqr = q3 - q1;
  let lowWhisker = sorted[0];
  for (let k = 0; k < n; k++) {
    if (sorted[k] >= q1 - 1.5 * iqr) {
      lowWhisker = sorted[k];
      break;
    }
  }
  let highWhisker = sorted[n - 1];
  for (let k = n - 1; k >= 0; k--) {
    if (sorted[k] <= q3 + 1.5 * iqr) {
      highWhisker = sorted[k];
      break;
    }
  }
  return {
    n,
    expressing,
    share: expressing / n,
    mean: sum / n,
    min: sorted[0],
    q1,
    median,
    q3,
    max: sorted[n - 1],
    lowWhisker,
    highWhisker,
  };
}

/** The violin's outline: a Gaussian kernel density over the group's values
 *  along `domain`, highest point 1. Null with fewer than 3 distinct values,
 *  where a density says nothing a box does not. */
export function violinShape(
  values: ArrayLike<number>,
  cells: ArrayLike<number>,
  domain: [number, number],
): { value: number; density: number }[] | null {
  const n = cells.length;
  const distinct = new Set<number>();
  for (let k = 0; k < n && distinct.size < 3; k++) distinct.add(values[cells[k]]);
  if (distinct.size < 3) return null;
  const [lo, hi] = domain;
  const span = hi - lo;
  if (!(span > 0)) return null;

  let sum = 0;
  for (let k = 0; k < n; k++) sum += values[cells[k]];
  const mean = sum / n;
  let squares = 0;
  for (let k = 0; k < n; k++) squares += (values[cells[k]] - mean) ** 2;
  const sd = Math.sqrt(squares / (n - 1));
  const stats = groupStats(values, cells)!;
  // Silverman's rule; a gene that is mostly zero has no IQR, so fall back to the spread.
  const spread = stats.q3 > stats.q1 ? Math.min(sd, (stats.q3 - stats.q1) / 1.34) : sd;
  const bandwidth = 0.9 * spread * n ** -0.2 || span / 50;

  const binWidth = span / VIOLIN_BINS;
  const counts = new Float64Array(VIOLIN_BINS);
  for (let k = 0; k < n; k++) {
    const bin = Math.min(VIOLIN_BINS - 1, Math.max(0, Math.floor((values[cells[k]] - lo) / binWidth)));
    counts[bin]++;
  }
  const out: { value: number; density: number }[] = [];
  let peak = 0;
  for (let s = 0; s <= VIOLIN_STEPS; s++) {
    const x = lo + (span * s) / VIOLIN_STEPS;
    let density = 0;
    for (let b = 0; b < VIOLIN_BINS; b++) {
      if (counts[b] === 0) continue;
      const z = (x - (lo + (b + 0.5) * binWidth)) / bandwidth;
      density += counts[b] * Math.exp(-0.5 * z * z);
    }
    out.push({ value: x, density });
    if (density > peak) peak = density;
  }
  return out.map((p) => ({ value: p.value, density: peak > 0 ? p.density / peak : 0 }));
}

/** The genes the view opens on: each cell type's first marker, then its second,
 *  without repeats; else the genes passing the DE cuts in the most cell types;
 *  else none. */
export function startingGenes(
  markersByCellType: readonly (ClusterMarkers | null)[],
  deGenes: readonly string[],
): { genes: string[]; source: "markers" | "de" | "none" } {
  const genes: string[] = [];
  const seen = new Set<string>();
  for (let rank = 0; rank < MARKER_RANKS; rank++) {
    for (const markers of markersByCellType) {
      const gene = markers?.top[rank]?.gene;
      if (!gene || seen.has(gene)) continue;
      seen.add(gene);
      genes.push(gene);
      if (genes.length >= MAX_MARKER_GENES) return { genes, source: "markers" };
    }
  }
  if (genes.length > 0) return { genes, source: "markers" };
  const fromDe = [...new Set(deGenes)].slice(0, MAX_DE_GENES);
  return fromDe.length > 0 ? { genes: fromDe, source: "de" } : { genes: [], source: "none" };
}

/** Where each mean falls between 0 and the top of its scale, for the dot's
 *  colour, and the top of each gene's scale: its own highest mean, or the
 *  highest mean of all genes. */
export function colourScale(means: number[][], perGene: boolean): { t: number[][]; max: number[] } {
  const rowMax = means.map((row) => Math.max(0, ...row));
  const overall = Math.max(0, ...rowMax);
  const max = rowMax.map((own) => (perGene ? own : overall));
  const t = means.map((row, r) => row.map((mean) => (max[r] > 0 ? mean / max[r] : 0)));
  return { t, max };
}

export interface TableRow {
  gene: string;
  cellType: string;
  part: string | null;
  stats: GroupStats;
}

/** One row per gene read and group, in the order given. */
export function tableRows(
  genes: readonly string[],
  values: ReadonlyMap<string, Float32Array>,
  groups: readonly CellGroup[],
): TableRow[] {
  const rows: TableRow[] = [];
  for (const gene of genes) {
    const own = values.get(gene);
    if (!own) continue;
    for (const group of groups) {
      const stats = groupStats(own, group.cells);
      if (stats) rows.push({ gene, cellType: group.cellType, part: group.part, stats });
    }
  }
  return rows;
}

const CSV_HEADER = "gene,cell_type,group,cells,expressing,share_expressing,mean,median,q1,q3";

const csvField = (text: string) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text);
const csvNumber = (x: number) => String(Number(x.toFixed(4)));

export function csvText(rows: readonly TableRow[]): string {
  const lines = rows.map(({ gene, cellType, part, stats }) =>
    [
      csvField(gene),
      csvField(cellType),
      csvField(part ?? ""),
      String(stats.n),
      String(stats.expressing),
      csvNumber(stats.share),
      csvNumber(stats.mean),
      csvNumber(stats.median),
      csvNumber(stats.q1),
      csvNumber(stats.q3),
    ].join(","),
  );
  return [CSV_HEADER, ...lines].join("\n") + "\n";
}
