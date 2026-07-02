"use client";

import { speciesColor } from "./constants";

export type SpeciesOption = { species: string; n_proteins: number };

/**
 * Species selector for the AI Orthologs KNN.
 *
 * Empty selection = all species (the unfiltered global KNN), so every chip
 * renders active in that state. Clicking a chip narrows the search to that
 * species (and any others you add); the "All" reset returns to the unfiltered
 * view. The selection drives knn_search_esm2's species_filter, so the search
 * returns the nearest matches WITHIN the chosen species, not a post-hoc filter.
 */
export function SpeciesFilter({
  options,
  selected,
  onToggle,
  onReset,
}: {
  options: SpeciesOption[];
  selected: ReadonlySet<string>;
  onToggle: (species: string) => void;
  onReset: () => void;
}) {
  if (options.length === 0) return null;
  const allActive = selected.size === 0;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-neutral-500">Species</span>
      <button
        type="button"
        onClick={onReset}
        disabled={allActive}
        className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
          allActive
            ? "border-blue-500 bg-blue-50 text-blue-700"
            : "border-stone-300 bg-white text-neutral-500 hover:bg-stone-50"
        }`}
        aria-pressed={allActive}
      >
        All
      </button>
      {options.map((o) => {
        const active = allActive || selected.has(o.species);
        return (
          <button
            key={o.species}
            type="button"
            onClick={() => onToggle(o.species)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "border-blue-500 bg-blue-50 text-blue-700"
                : "border-stone-300 bg-white text-neutral-500 hover:bg-stone-50"
            }`}
            aria-pressed={active}
            title={`${o.n_proteins} protein${o.n_proteins === 1 ? "" : "s"} with embeddings`}
          >
            <span
              className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
              style={{ backgroundColor: speciesColor(o.species), opacity: active ? 1 : 0.4 }}
              aria-hidden
            />
            {o.species}
            <span className="ml-1.5 text-[10px] text-neutral-400">{o.n_proteins}</span>
          </button>
        );
      })}
    </div>
  );
}
