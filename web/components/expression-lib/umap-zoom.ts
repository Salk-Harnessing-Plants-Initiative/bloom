/** The map's zoom range, and the log-scale slider that moves through it. */

/** How far the map can be zoomed, in each direction. */
export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 50;

/** Slider positions run from 0 to this. */
export const ZOOM_SLIDER_STEPS = 1000;

/** One press of + or − multiplies or divides the zoom by this. */
export const ZOOM_BUTTON_FACTOR = 1.5;

const LOG_MIN = Math.log(MIN_ZOOM);
const LOG_SPAN = Math.log(MAX_ZOOM) - LOG_MIN;

/** A zoom held inside the bounds; anything not a number is the whole map. */
export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

/** Where a zoom sits on the slider. Equal moves along it are equal zoom ratios,
 *  so 1× to 2× takes as much travel as 20× to 40×. */
export function zoomToSlider(zoom: number): number {
  return Math.round(((Math.log(clampZoom(zoom)) - LOG_MIN) / LOG_SPAN) * ZOOM_SLIDER_STEPS);
}

export function sliderToZoom(position: number): number {
  // The ends are the bounds exactly, so the + and − buttons disable there.
  if (position <= 0) return MIN_ZOOM;
  if (position >= ZOOM_SLIDER_STEPS) return MAX_ZOOM;
  return clampZoom(Math.exp(LOG_MIN + (position / ZOOM_SLIDER_STEPS) * LOG_SPAN));
}

export function stepZoom(zoom: number, direction: 1 | -1): number {
  return clampZoom(direction > 0 ? zoom * ZOOM_BUTTON_FACTOR : zoom / ZOOM_BUTTON_FACTOR);
}

export function formatZoom(zoom: number): string {
  return zoom < 10 ? `${zoom.toFixed(1)}×` : `${Math.round(zoom)}×`;
}
