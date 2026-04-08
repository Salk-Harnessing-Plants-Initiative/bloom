"use client";

import { SPECIES_BY_TYPE, getSpeciesColor } from "./constants";
import type { KnnResult } from "./GeneSearch";

interface SpeciesBreakdownProps {
  results: KnnResult[];
}

export default function SpeciesBreakdown({ results }: SpeciesBreakdownProps) {
  if (results.length === 0) return null;

  const counts: Record<string, number> = {};
  for (const r of results) {
    counts[r.species] = (counts[r.species] ?? 0) + 1;
  }

  return (
    <div className="space-y-2">
      {Object.entries(SPECIES_BY_TYPE).map(([type, species]) => {
        const hasResults = species.some((s) => (counts[s] ?? 0) > 0);
        if (!hasResults) return null;
        return (
          <div key={type}>
            <span className="text-xs text-neutral-400 font-medium">{type}</span>
            <div className="flex gap-2 flex-wrap mt-1">
              {species.map((s) => (
                <div
                  key={s}
                  className="flex items-center gap-3 bg-white border border-stone-200 rounded-lg px-4 py-2.5"
                  style={{ borderLeftWidth: 4, borderLeftColor: getSpeciesColor(s) }}
                >
                  <span className="capitalize text-sm font-medium text-neutral-700">
                    {s}
                  </span>
                  <span className="text-lg font-semibold text-neutral-900">
                    {counts[s] ?? 0}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
