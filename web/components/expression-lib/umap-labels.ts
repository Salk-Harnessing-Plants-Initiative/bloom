/**
 * Where to write each group's name on a map: on the densest part of its
 * largest patch, so a cell type split across two islands is named on the
 * bigger one rather than in the empty space between them.
 */

/** The map is split into this many squares a side to find each group's densest patch. */
export const LABEL_GRID = 40;
/** A group needs this many points in its densest square to be named. */
export const MIN_LABEL_POINTS = 5;

export interface LabelAnchor {
  /** The group's code. */
  level: number;
  /** Where to write its name, in the map's own coordinates. */
  x: number;
  y: number;
}

/** One anchor per group with enough shown points: the mean position of its
 *  points in the grid square that holds most of them. Codes outside
 *  0..nLevels-1 belong to no group. */
export function labelAnchors(
  positions: Float32Array,
  codes: ArrayLike<number>,
  nLevels: number,
  visibility: Float32Array | null,
  grid: number = LABEL_GRID,
  minPoints: number = MIN_LABEL_POINTS,
): LabelAnchor[] {
  const n = codes.length;
  if (nLevels <= 0 || n === 0) return [];
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 2];
    const y = positions[i * 2 + 1];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const squares = grid * grid;
  const squareOf = (i: number) => {
    const bx = Math.min(grid - 1, Math.floor(((positions[i * 2] - minX) / spanX) * grid));
    const by = Math.min(grid - 1, Math.floor(((positions[i * 2 + 1] - minY) / spanY) * grid));
    return by * grid + bx;
  };
  const counted = (i: number) => {
    if (visibility && visibility[i] === 0) return -1;
    const code = codes[i];
    return code >= 0 && code < nLevels ? code : -1;
  };

  const counts = new Int32Array(nLevels * squares);
  for (let i = 0; i < n; i++) {
    const code = counted(i);
    if (code >= 0) counts[code * squares + squareOf(i)]++;
  }

  const best = new Int32Array(nLevels).fill(-1);
  for (let level = 0; level < nLevels; level++) {
    let most = minPoints - 1;
    for (let s = 0; s < squares; s++) {
      const c = counts[level * squares + s];
      if (c > most) {
        most = c;
        best[level] = s;
      }
    }
  }

  const sumX = new Float64Array(nLevels);
  const sumY = new Float64Array(nLevels);
  const inBest = new Int32Array(nLevels);
  for (let i = 0; i < n; i++) {
    const code = counted(i);
    if (code < 0 || best[code] < 0 || squareOf(i) !== best[code]) continue;
    sumX[code] += positions[i * 2];
    sumY[code] += positions[i * 2 + 1];
    inBest[code]++;
  }

  const anchors: LabelAnchor[] = [];
  for (let level = 0; level < nLevels; level++) {
    if (inBest[level] > 0) {
      anchors.push({ level, x: sumX[level] / inBest[level], y: sumY[level] / inBest[level] });
    }
  }
  return anchors;
}

/** Where a map position lands on the canvas, in CSS pixels: the shader draws
 *  it at `(position + translate) * zoom * fit` in clip space. */
export function projectXY(
  x: number,
  y: number,
  view: { zoom: number; translate: [number, number]; width: number; height: number },
  fit: [number, number],
): { x: number; y: number } {
  const clipX = (x + view.translate[0]) * view.zoom * fit[0];
  const clipY = (y + view.translate[1]) * view.zoom * fit[1];
  return { x: ((clipX + 1) / 2) * view.width, y: ((1 - clipY) / 2) * view.height };
}
