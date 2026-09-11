"use client";

import { useState } from "react";

export interface MapLabel {
  text: string;
  /** Where to write it, in the map's own coordinates. */
  x: number;
  y: number;
  /** A green badge after the name, such as its transgene-positive cells. */
  badge?: string;
}

interface Props {
  labels: MapLabel[];
  /** Places a map position on the canvas, in CSS pixels. */
  project: (x: number, y: number) => { x: number; y: number };
  width: number;
  height: number;
}

/** Group names written over a map, each on its cluster, with a button to hide
 *  them. They follow the map as it is zoomed and panned. */
export function UmapLabelLayer({ labels, project, width, height }: Props) {
  const [shown, setShown] = useState(true);
  if (labels.length === 0) return null;

  return (
    <>
      {shown &&
        labels.map((label, i) => {
          const at = project(label.x, label.y);
          if (at.x < 0 || at.x > width || at.y < 0 || at.y > height) return null;
          return (
            <div
              key={`${label.text}-${i}`}
              data-testid="umap-label"
              className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-[11px] font-semibold leading-none text-white"
              style={{
                left: at.x,
                top: at.y,
                textShadow: "0 0 3px rgba(0,0,0,0.95), 0 0 6px rgba(0,0,0,0.75)",
              }}
            >
              {label.text}
              {label.badge && (
                <span
                  className="ml-1.5 rounded-full bg-emerald-500 px-1.5 py-0.5 align-middle text-[10px] font-bold text-white ring-1 ring-emerald-200"
                  style={{ textShadow: "none" }}
                >
                  {label.badge}
                </span>
              )}
            </div>
          );
        })}
      <button
        type="button"
        onClick={() => setShown((on) => !on)}
        aria-pressed={shown}
        className="absolute left-3 top-3 rounded-md bg-zinc-900/85 px-2 py-1 text-[11px] text-zinc-200 ring-1 ring-white/10 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-1 focus-visible:outline-lime-400"
      >
        {shown ? "Hide labels" : "Show labels"}
      </button>
    </>
  );
}

export default UmapLabelLayer;
