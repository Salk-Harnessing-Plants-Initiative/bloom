"use client";

import { useMemo, useState } from "react";
import { PlateImage } from "./PlateImage";

export interface TimePoint {
  scan_id: number;
  capture_date: string;
  cycle_number: number | null;
  object_path: string | null;
}

interface PlateTimeSeriesProps {
  points: TimePoint[];
}

const DEFAULT_WINDOW_HOURS = 2;
const DEFAULT_MAX_FRAMES = 6;

export function PlateTimeSeries({ points }: PlateTimeSeriesProps) {
  const [showAll, setShowAll] = useState(false);
  const [selectedCycle, setSelectedCycle] = useState<number | null>(null);

  const sorted = useMemo(
    () =>
      [...points].sort((a, b) =>
        a.capture_date.localeCompare(b.capture_date),
      ),
    [points],
  );

  const cycleOptions = useMemo(
    () =>
      sorted
        .map((p) => p.cycle_number)
        .filter((c): c is number => c !== null)
        .sort((a, b) => a - b),
    [sorted],
  );

  const selectedPoint = useMemo(
    () =>
      selectedCycle !== null
        ? sorted.find((p) => p.cycle_number === selectedCycle) ?? null
        : null,
    [sorted, selectedCycle],
  );

  const defaultPoints = useMemo(() => {
    if (sorted.length === 0) return [] as TimePoint[];
    const latestMs = new Date(
      sorted[sorted.length - 1].capture_date,
    ).getTime();
    const cutoff = latestMs - DEFAULT_WINDOW_HOURS * 3600 * 1000;
    const within = sorted.filter(
      (p) => new Date(p.capture_date).getTime() >= cutoff,
    );
    return within.slice(-DEFAULT_MAX_FRAMES);
  }, [sorted]);

  const visible = showAll ? sorted : defaultPoints;
  const hiddenCount = sorted.length - visible.length;

  if (sorted.length === 0) {
    return (
      <div className="text-sm italic text-stone-400">
        No time points captured yet.
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <div className="text-sm text-stone-500">
          Showing {visible.length} of {sorted.length} time point
          {sorted.length === 1 ? "" : "s"}
          {!showAll && hiddenCount > 0 && (
            <span className="ml-1 text-xs text-stone-400">
              (last {DEFAULT_WINDOW_HOURS}h, max {DEFAULT_MAX_FRAMES} frames)
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {cycleOptions.length > 0 && (
            <label className="flex items-center gap-2 text-xs text-stone-500">
              View cycle
              <select
                value={selectedCycle ?? ""}
                onChange={(e) =>
                  setSelectedCycle(
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                className="rounded-md border border-stone-300 bg-white px-2 py-0.5 text-xs text-stone-700"
              >
                <option value="">—</option>
                {cycleOptions.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          )}
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setShowAll(!showAll)}
              className="text-xs font-medium text-lime-700 hover:underline"
            >
              {showAll ? "Show recent only" : `Show all ${sorted.length}`}
            </button>
          )}
        </div>
      </div>

      {selectedPoint && (
        <div className="mb-4 rounded-md border border-stone-200 bg-stone-50 p-3">
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <div className="text-sm font-medium text-stone-700">
              Cycle {selectedPoint.cycle_number} ·{" "}
              <span className="text-stone-500 font-normal">
                {formatStamp(selectedPoint.capture_date)}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setSelectedCycle(null)}
              className="text-xs text-stone-500 hover:underline"
            >
              Clear
            </button>
          </div>
          <PlateImage
            path={selectedPoint.object_path}
            alt={`Cycle ${selectedPoint.cycle_number}`}
            className="w-full max-w-[640px] aspect-square"
          />
        </div>
      )}

      <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2">
        {visible.map((p) => (
          <div key={p.scan_id} className="shrink-0">
            <PlateImage
              path={p.object_path}
              alt={`Time point ${p.cycle_number ?? p.scan_id}`}
              className="w-[280px] h-[280px]"
            />
            <div className="mt-1 w-[280px] text-center text-xs text-stone-500">
              {formatStamp(p.capture_date)}
            </div>
            {p.cycle_number !== null && (
              <div className="w-[280px] text-center text-[10px] uppercase tracking-wide text-stone-400">
                cycle {p.cycle_number}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function formatStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
