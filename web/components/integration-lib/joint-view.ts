/**
 * Where the joint map's points land on its canvas, and which one is under the
 * cursor. The shader and these must agree: a point is drawn at
 * `(position + translate) * zoom * fit` in clip space.
 */

export interface JointView {
  zoom: number;
  translate: [number, number];
  /** The canvas's size in CSS pixels. */
  width: number;
  height: number;
}

/** How near the cursor a point must be, in CSS pixels, to be the one picked. */
export const PICK_RADIUS_PX = 8;

/** The canvas is square to its width, up to the window's height less this. */
export const VIEWPORT_MARGIN = 140;
export const MIN_MAP_HEIGHT = 520;
export const MAX_MAP_HEIGHT = 1100;

/** Scales clip space so a unit is as long across as it is up, whatever the
 *  canvas's shape: the map fits the shorter side instead of stretching. */
export function fitFor(width: number, height: number): [number, number] {
  if (width <= 0 || height <= 0) return [1, 1];
  return width >= height ? [height / width, 1] : [1, width / height];
}

export function projectPoint(
  positions: Float32Array,
  index: number,
  view: JointView,
): { x: number; y: number } {
  const [fx, fy] = fitFor(view.width, view.height);
  const clipX = (positions[index * 2] + view.translate[0]) * view.zoom * fx;
  const clipY = (positions[index * 2 + 1] + view.translate[1]) * view.zoom * fy;
  return { x: ((clipX + 1) / 2) * view.width, y: ((1 - clipY) / 2) * view.height };
}

/** The shown point nearest the cursor, or null when none is within the radius. */
export function pickPoint(
  positions: Float32Array,
  visibility: Float32Array | null,
  view: JointView,
  cursor: { x: number; y: number },
  radiusPx: number = PICK_RADIUS_PX,
): number | null {
  const { width, height, zoom, translate } = view;
  if (width <= 0 || height <= 0) return null;
  const [fx, fy] = fitFor(width, height);
  const sx = (zoom * fx * width) / 2;
  const sy = (zoom * fy * height) / 2;
  let best: number | null = null;
  let bestDistance = radiusPx * radiusPx;
  for (let i = 0; i < positions.length / 2; i++) {
    if (visibility && visibility[i] === 0) continue;
    const dx = (positions[i * 2] + translate[0]) * sx + width / 2 - cursor.x;
    const dy = height / 2 - (positions[i * 2 + 1] + translate[1]) * sy - cursor.y;
    const distance = dx * dx + dy * dy;
    if (distance <= bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  return best;
}

/** How far a drag of (dx, dy) CSS pixels moves the map, in position units. */
export function panBy(dx: number, dy: number, view: JointView): [number, number] {
  const [fx, fy] = fitFor(view.width, view.height);
  return [
    ((dx / view.width) * 2) / (view.zoom * fx),
    -((dy / view.height) * 2) / (view.zoom * fy),
  ];
}

/** In full screen the canvas runs down to this far above the window's bottom,
 *  leaving room for the line under it. */
export const FILL_BOTTOM_GAP = 48;

/** The canvas's height in full screen: from its top to the window's bottom. */
export function fillHeightFor(top: number, viewportHeight: number): number {
  return Math.max(MIN_MAP_HEIGHT, Math.round(viewportHeight - top - FILL_BOTTOM_GAP));
}

/** The canvas's height: square to its width where the window allows. */
export function mapHeightFor(width: number, viewportHeight: number): number {
  const wanted = Math.min(width, viewportHeight - VIEWPORT_MARGIN);
  return Math.round(Math.min(MAX_MAP_HEIGHT, Math.max(MIN_MAP_HEIGHT, wanted)));
}
