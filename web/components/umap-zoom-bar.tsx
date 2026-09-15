"use client";

import {
  formatZoom,
  MAX_ZOOM,
  MIN_ZOOM,
  sliderToZoom,
  stepZoom,
  ZOOM_SLIDER_STEPS,
  zoomToSlider,
} from "@/components/expression-lib/umap-zoom";

interface Props {
  zoom: number;
  onZoomChange: (zoom: number) => void;
  /** Back to the whole map: zoom 1, centred. */
  onReset: () => void;
}

const BUTTON =
  "flex h-6 w-6 items-center justify-center rounded text-sm leading-none text-zinc-200 " +
  "hover:bg-white/10 disabled:cursor-default disabled:text-zinc-600 disabled:hover:bg-transparent " +
  "focus-visible:outline focus-visible:outline-1 focus-visible:outline-lime-400";

/** A zoom slider over the corner of a map, for moving further than the wheel
 *  comfortably goes. It zooms about the middle of the view. */
export function UmapZoomBar({ zoom, onZoomChange, onReset }: Props) {
  return (
    <div
      data-testid="umap-zoom-bar"
      className="absolute bottom-3 right-3 flex items-center gap-1 rounded-md bg-zinc-900/85 px-1.5 py-1 shadow-md ring-1 ring-white/10 backdrop-blur-sm"
    >
      <button
        type="button"
        aria-label="Zoom out"
        onClick={() => onZoomChange(stepZoom(zoom, -1))}
        disabled={zoom <= MIN_ZOOM}
        className={BUTTON}
      >
        −
      </button>
      <input
        type="range"
        aria-label="Zoom"
        aria-valuetext={formatZoom(zoom)}
        min={0}
        max={ZOOM_SLIDER_STEPS}
        step={1}
        value={zoomToSlider(zoom)}
        onChange={(e) => onZoomChange(sliderToZoom(Number(e.target.value)))}
        className="h-1 w-32 cursor-pointer accent-lime-400"
      />
      <button
        type="button"
        aria-label="Zoom in"
        onClick={() => onZoomChange(stepZoom(zoom, 1))}
        disabled={zoom >= MAX_ZOOM}
        className={BUTTON}
      >
        +
      </button>
      <span className="w-9 text-right text-[11px] tabular-nums text-zinc-400">
        {formatZoom(zoom)}
      </span>
      <button
        type="button"
        aria-label="Fit the whole map"
        title="Fit the whole map"
        onClick={onReset}
        className="ml-0.5 rounded px-1.5 py-0.5 text-[11px] text-zinc-300 hover:bg-white/10 focus-visible:outline focus-visible:outline-1 focus-visible:outline-lime-400"
      >
        Fit
      </button>
    </div>
  );
}

export default UmapZoomBar;
