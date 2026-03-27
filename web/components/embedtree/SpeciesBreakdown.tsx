"use client";

import { ALL_SPECIES, getSpeciesColor } from "./constants";
import type { KnnResult } from "./GeneSearch";

interface SpeciesBreakdownProps {
  results: KnnResult[];
}

export default function SpeciesBreakdown({ results }: SpeciesBreakdownProps) {
  if (results.length === 0) return null;

  const counts: Record<string, number> = {};
  for (const species of ALL_SPECIES) {
    counts[species] = 0;
  }
  for (const r of results) {
    counts[r.species] = (counts[r.species] ?? 0) + 1;
  }

  return (
    <div className="flex gap-3 flex-wrap">
      {ALL_SPECIES.map((species) => (
        <div
          key={species}
          className="flex items-center gap-3 bg-white border border-stone-200 rounded-lg px-4 py-2.5"
          style={{ borderLeftWidth: 4, borderLeftColor: getSpeciesColor(species) }}
        >
          <span className="capitalize text-sm font-medium text-neutral-700">
            {species}
          </span>
          <span className="text-lg font-semibold text-neutral-900">
            {counts[species]}
          </span>
        </div>
      ))}
    </div>
  );
}
