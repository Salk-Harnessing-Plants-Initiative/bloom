"use client";

import { speciesColor } from "./constants";
import type { KnnNeighbor } from "./knn-graph";

type Props = {
  queryUid: string;
  querySpecies: string | null;
  queryGeneId: string | null;
  neighbors: KnnNeighbor[];
  onSelectNeighbor?: (n: KnnNeighbor) => void;
  hoveredUid?: string | null;
  onHoverRow?: (uid: string | null) => void;
};

/**
 * Ranked KNN results table. Renders the query gene pinned at the top
 * with a "QUERY" badge, then the K neighbors ordered by descending
 * cosine similarity (the order PostgREST returns them in).
 *
 * Row click pivots the page to that neighbor — same behavior as
 * clicking a graph node. Row hover fires `onHoverRow` so the parent
 * can highlight the matching graph node in lockstep.
 */
export function ResultsPanel({
  queryUid,
  querySpecies,
  queryGeneId,
  neighbors,
  onSelectNeighbor,
  hoveredUid,
  onHoverRow,
}: Props) {
  if (neighbors.length === 0) {
    return null;
  }

  return (
    <div className="w-full overflow-hidden rounded-md border border-stone-200 bg-white">
      <div className="border-b border-stone-200 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Ranked neighbors
      </div>
      <div className="max-h-72 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="w-12 px-3 py-2 text-right font-medium">Rank</th>
              <th className="px-3 py-2 text-left font-medium">Gene</th>
              <th className="px-3 py-2 text-left font-medium">Species</th>
              <th className="w-32 px-3 py-2 text-right font-medium">
                Cosine similarity
              </th>
              <th className="w-32 px-3 py-2 text-right font-medium">
                Cosine distance
              </th>
            </tr>
          </thead>
          <tbody>
            {/* Query row, pinned at the top. */}
            <tr
              className={`border-t border-stone-100 bg-amber-50/40 ${
                hoveredUid === queryUid ? "bg-amber-100/60" : ""
              }`}
              onMouseEnter={() => onHoverRow?.(queryUid)}
              onMouseLeave={() => onHoverRow?.(null)}
            >
              <td className="px-3 py-1.5 text-right font-mono text-xs text-neutral-400">
                —
              </td>
              <td className="px-3 py-1.5 font-mono text-neutral-800">
                {queryGeneId ?? queryUid}
                <span className="ml-2 rounded bg-amber-200 px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-wider text-amber-900">
                  Query
                </span>
              </td>
              <td className="px-3 py-1.5">
                <SpeciesCell species={querySpecies} />
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-neutral-500">
                1.000
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-neutral-500">
                0.000
              </td>
            </tr>
            {neighbors.map((n, i) => {
              const distance = 1 - n.similarity;
              const isHover = hoveredUid === n.uid;
              return (
                <tr
                  key={n.uid}
                  onClick={() => onSelectNeighbor?.(n)}
                  onMouseEnter={() => onHoverRow?.(n.uid)}
                  onMouseLeave={() => onHoverRow?.(null)}
                  className={`cursor-pointer border-t border-stone-100 transition-colors ${
                    isHover ? "bg-blue-50" : "hover:bg-stone-50"
                  }`}
                >
                  <td className="px-3 py-1.5 text-right font-mono text-xs text-neutral-500">
                    {i + 1}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-neutral-800">
                    {n.gene_id ?? n.uid}
                  </td>
                  <td className="px-3 py-1.5">
                    <SpeciesCell species={n.species} />
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                    {n.similarity.toFixed(3)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                    {distance.toFixed(3)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SpeciesCell({ species }: { species: string | null }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-neutral-700">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{ backgroundColor: speciesColor(species) }}
        aria-hidden
      />
      {species ?? "—"}
    </span>
  );
}
