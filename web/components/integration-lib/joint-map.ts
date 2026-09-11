/**
 * Turning a joint embedding's arrays into what its map draws and its filters count.
 *
 * Every filter row -- which dataset a point came from, and each label -- has one
 * shape: its values, and each point's index into them, -1 for a point with no
 * value. Hiding, focusing, colouring and counting are written once against it.
 */

/** A filter row: its values, and each point's index into them (-1 for none). */
export interface LabelRow {
  key: string;
  levels: string[];
  codes: ArrayLike<number>;
}

/** The row saying which dataset each point came from. */
export const DATASET_KEY = "dataset";

/** A label read from each query point's own cell, so any map with a query has it. */
export const GENOTYPE_KEY = "genotype";

/** Values hidden, or focused on, per row: indexes into that row's levels. */
export type ValueSets = ReadonlyMap<string, ReadonlySet<number>>;

/** Gives the map's bounding box a small border. */
const NORMALIZE_PADDING = 1.05;

/** Points with no value in the row the map is coloured by: faint grey. */
export const NO_VALUE_RGB: [number, number, number] = [0.45, 0.45, 0.48];
export const NO_VALUE_ALPHA = 0.3;

type Rgb = [number, number, number];

/** Twenty colours that read on the map's dark ground; later values go round
 *  the colour wheel by the golden angle, so neighbours never look alike. */
const BASE_PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#a0cbe8", "#ffbe7d", "#ff9d9a", "#86bcb6", "#8cd17d",
  "#f1ce63", "#d4a6c8", "#fabfd2", "#d7b5a6", "#79706e",
];
const GOLDEN_ANGLE = 137.508;

function hexRgb(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

function hslRgb(h: number, s: number, l: number): Rgb {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

/** A colour per value, the same for a value whatever the row's length. */
export function palette(n: number): Rgb[] {
  const out: Rgb[] = [];
  for (let i = 0; i < n; i++) {
    out.push(
      i < BASE_PALETTE.length
        ? hexRgb(BASE_PALETTE[i])
        : hslRgb(((i - BASE_PALETTE.length) * GOLDEN_ANGLE) % 360, 0.6, 0.62),
    );
  }
  return out;
}

export function rgbCss([r, g, b]: Rgb): string {
  return `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
}

/** The dataset row, its values the member datasets in member order. */
export function datasetRow(
  members: { ordinal: number; name: string }[],
  memberOrdinals: ArrayLike<number>,
): LabelRow {
  const sorted = [...members].sort((a, b) => a.ordinal - b.ordinal);
  const index = new Map(sorted.map((m, i) => [m.ordinal, i]));
  const codes = new Int16Array(memberOrdinals.length);
  for (let i = 0; i < codes.length; i++) codes[i] = index.get(memberOrdinals[i]) ?? -1;
  return { key: DATASET_KEY, levels: sorted.map((m) => m.name), codes };
}

/** Positions centred and scaled so the longer side fits the canvas. */
export function normalisePositions(x: ArrayLike<number>, y: ArrayLike<number>): Float32Array {
  const n = x.length;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    if (x[i] < minX) minX = x[i];
    if (x[i] > maxX) maxX = x[i];
    if (y[i] < minY) minY = y[i];
    if (y[i] > maxY) maxY = y[i];
  }
  const centreX = (minX + maxX) / 2;
  const centreY = (minY + maxY) / 2;
  const range = Math.max(maxX - minX, maxY - minY) || 1;
  const scale = 2 / (range * NORMALIZE_PADDING);
  const out = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    out[i * 2] = (x[i] - centreX) * scale;
    out[i * 2 + 1] = (y[i] - centreY) * scale;
  }
  return out;
}

/** Each point's colour, from its value in the row the map is coloured by. */
export function packColours(row: LabelRow, colours: Rgb[]): Float32Array {
  const n = row.codes.length;
  const out = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    const code = row.codes[i];
    const [r, g, b] = code >= 0 && code < colours.length ? colours[code] : NO_VALUE_RGB;
    out[i * 4] = r;
    out[i * 4 + 1] = g;
    out[i * 4 + 2] = b;
    out[i * 4 + 3] = code >= 0 ? 1 : NO_VALUE_ALPHA;
  }
  return out;
}

/** The rows with at least one value chosen, each with a lookup by value index. */
function chosenRows(rows: readonly LabelRow[], sets: ValueSets) {
  const out: { index: number; codes: ArrayLike<number>; chosen: Uint8Array }[] = [];
  rows.forEach((row, index) => {
    const set = sets.get(row.key);
    if (!set || set.size === 0) return;
    const chosen = new Uint8Array(row.levels.length);
    for (const v of set) if (v >= 0 && v < chosen.length) chosen[v] = 1;
    out.push({ index, codes: row.codes, chosen });
  });
  return out;
}

const pointCount = (rows: readonly LabelRow[]) => rows[0]?.codes.length ?? 0;

/** Which points are drawn: 0 for a point any row hides. A point with no value
 *  in a row is never hidden by it: the row says nothing about that point. */
export function packVisibility(rows: readonly LabelRow[], hidden: ValueSets): Float32Array {
  const out = new Float32Array(pointCount(rows)).fill(1);
  for (const { codes, chosen } of chosenRows(rows, hidden)) {
    for (let i = 0; i < out.length; i++) {
      const code = codes[i];
      if (code >= 0 && chosen[code]) out[i] = 0;
    }
  }
  return out;
}

/** Which points meet the focus: in every row with a value chosen, the point's
 *  value is one of those chosen. With nothing chosen, every point does. */
export function packFocus(rows: readonly LabelRow[], focused: ValueSets): Float32Array {
  const out = new Float32Array(pointCount(rows)).fill(1);
  for (const { codes, chosen } of chosenRows(rows, focused)) {
    for (let i = 0; i < out.length; i++) {
      const code = codes[i];
      if (code < 0 || !chosen[code]) out[i] = 0;
    }
  }
  return out;
}

export function focusIsChosen(sets: ValueSets): boolean {
  for (const values of sets.values()) if (values.size > 0) return true;
  return false;
}

/** Per row, how many points have each value, counting only the points the
 *  other rows leave on the map. A row's own hiding does not change its counts,
 *  so a hidden value still says how many points it would bring back. */
export function countLevels(
  rows: readonly LabelRow[],
  hidden: ValueSets,
): Map<string, number[]> {
  const counts = rows.map((row) => new Array<number>(row.levels.length).fill(0));
  const active = chosenRows(rows, hidden);
  const n = pointCount(rows);
  for (let i = 0; i < n; i++) {
    let rulers = 0;
    let ruler = -1;
    for (const { index, codes, chosen } of active) {
      const code = codes[i];
      if (code >= 0 && chosen[code]) {
        rulers++;
        ruler = index;
        if (rulers > 1) break;
      }
    }
    if (rulers > 1) continue;
    for (let r = 0; r < rows.length; r++) {
      if (rulers === 1 && r !== ruler) continue;
      const code = rows[r].codes[i];
      if (code >= 0) counts[r][code]++;
    }
  }
  return new Map(rows.map((row, r) => [row.key, counts[r]]));
}

export function countNoValue(row: LabelRow): number {
  let n = 0;
  for (let i = 0; i < row.codes.length; i++) if (row.codes[i] < 0) n++;
  return n;
}

/** How many points meet the focus among those still on the map. */
export function countFocused(
  rows: readonly LabelRow[],
  focused: ValueSets,
  hidden: ValueSets,
): number {
  const visible = packVisibility(rows, hidden);
  const focus = packFocus(rows, focused);
  let n = 0;
  for (let i = 0; i < visible.length; i++) if (visible[i] && focus[i]) n++;
  return n;
}

/** The focus in words, datasets first: "MYB41 and transgene_pos True". */
export function describeFocus(rows: readonly LabelRow[], focused: ValueSets): string {
  const ordered = [...rows].sort(
    (a, b) => Number(b.key === DATASET_KEY) - Number(a.key === DATASET_KEY),
  );
  const parts: string[] = [];
  for (const row of ordered) {
    const set = focused.get(row.key);
    if (!set || set.size === 0) continue;
    const listed = [...set].sort((a, b) => a - b).map((v) => row.levels[v]).join(" or ");
    parts.push(row.key === DATASET_KEY ? listed : `${row.key} ${listed}`);
  }
  return parts.join(" and ");
}

/** What every row says about one point: its value, or null for none. */
export function pointValues(
  index: number,
  rows: readonly LabelRow[],
): { key: string; value: string | null }[] {
  return rows.map((row) => {
    const code = row.codes[index];
    return { key: row.key, value: code >= 0 ? row.levels[code] ?? null : null };
  });
}

/** The row that colours every cell by its own dataset's cell type. */
export const CELL_TYPE_KEY = "cell type";

/** One setting per member dataset from the map's params, as [member ordinal,
 *  value]: {"<field>": {"0": "..."}}. Anything malformed is left out. */
function byMember(params: unknown, field: string): [number, string][] {
  if (!params || typeof params !== "object") return [];
  const raw = (params as Record<string, unknown>)[field];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const out: [number, string][] = [];
  for (const [ordinal, value] of Object.entries(raw)) {
    const n = Number(ordinal);
    if (Number.isInteger(n) && n >= 0 && typeof value === "string" && value) out.push([n, value]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}

/** Which label holds each dataset's cell types, as [member ordinal, label key],
 *  from the params' "cell_type_labels". */
export function cellTypeLabels(params: unknown): [number, string][] {
  return byMember(params, "cell_type_labels");
}

/** What this map calls each dataset where its own name would mislead, by member
 *  ordinal, from the params' "member_names": "MYB41 dataset" for a dataset named
 *  after its transgene though it holds every genotype. */
export function memberNames(params: unknown): Map<number, string> {
  return new Map(byMember(params, "member_names"));
}

/** One row from several: each cell takes its value from the label chosen for its
 *  own dataset, keyed here by the dataset's index in the dataset row. Atlases
 *  that keep cell types in different labels then share one colouring, and a
 *  name gets one value, so one colour, whichever dataset it comes from. */
export function combinedRow(
  key: string,
  datasets: LabelRow,
  sources: ReadonlyMap<number, LabelRow>,
): LabelRow {
  const n = datasets.codes.length;
  const valueAt = (i: number): string | null => {
    const source = sources.get(datasets.codes[i]);
    const code = source ? source.codes[i] : -1;
    return source && code >= 0 ? source.levels[code] ?? null : null;
  };
  const used = new Set<string>();
  for (let i = 0; i < n; i++) {
    const value = valueAt(i);
    if (value !== null) used.add(value);
  }
  const levels = [...used].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const index = new Map(levels.map((level, i) => [level, i]));
  const codes = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const value = valueAt(i);
    codes[i] = value === null ? -1 : index.get(value) ?? -1;
  }
  return { key, levels, codes };
}
