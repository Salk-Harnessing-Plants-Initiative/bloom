"use client";

import { getSpeciesColor } from "./constants";
import type { KnnResult } from "./GeneSearch";

interface KnnResultsProps {
  results: KnnResult[];
  queryUid: string;
  showWithin: boolean;
  showAcross: boolean;
}

function ResultTable({
  title,
  rows,
  showSpeciesColumn,
}: {
  title: string;
  rows: KnnResult[];
  showSpeciesColumn: boolean;
}) {
  if (rows.length === 0) {
    return (
      <div className="flex-1 min-w-0">
        <h4 className="text-sm font-semibold text-neutral-700 mb-2">{title}</h4>
        <p className="text-sm text-neutral-400">No results</p>
      </div>
    );
  }

  return (
    <div className="flex-1 min-w-0">
      <h4 className="text-sm font-semibold text-neutral-700 mb-2">
        {title} ({rows.length})
      </h4>
      <div className="border border-stone-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-stone-50 text-left text-neutral-500">
              <th className="px-3 py-2 font-medium w-12">Rank</th>
              {showSpeciesColumn && (
                <th className="px-3 py-2 font-medium">Species</th>
              )}
              <th className="px-3 py-2 font-medium">Gene ID</th>
              <th className="px-3 py-2 font-medium text-right">Similarity</th>
              <th className="px-3 py-2 font-medium text-right">Distance</th>
              <th className="px-3 py-2 font-medium text-center">OrthoFinder</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.uid}
                className="border-t border-stone-100 hover:bg-stone-50"
              >
                <td className="px-3 py-1.5 text-neutral-400">{row.rank}</td>
                {showSpeciesColumn && (
                  <td className="px-3 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{
                          backgroundColor: getSpeciesColor(row.species),
                        }}
                      />
                      <span className="capitalize text-neutral-700">
                        {row.species}
                      </span>
                    </span>
                  </td>
                )}
                <td className="px-3 py-1.5 font-mono text-neutral-800">
                  {row.gene_id}
                </td>
                <td className="px-3 py-1.5 text-right text-neutral-700">
                  {row.similarity.toFixed(4)}
                </td>
                <td className="px-3 py-1.5 text-right text-neutral-500">
                  {(1 - row.similarity).toFixed(4)}
                </td>
                <td className="px-3 py-1.5 text-center">
                  {row.orthogroup ? (
                    <span
                      className={`inline-block px-1.5 py-0.5 text-xs font-medium rounded border ${
                        row.orthogroupShared
                          ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                          : "bg-stone-50 text-stone-500 border-stone-200"
                      }`}
                    >
                      {row.orthogroup}
                    </span>
                  ) : (
                    <span className="text-neutral-300">--</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function KnnResults({
  results,
  queryUid,
  showWithin,
  showAcross,
}: KnnResultsProps) {
  if (results.length === 0) return null;

  const querySpecies = queryUid.split(":")[0];
  const withinResults = results.filter((r) => r.species === querySpecies);
  const acrossResults = results.filter((r) => r.species !== querySpecies);

  return (
    <div className="space-y-2">
      <div className="flex gap-4 flex-col md:flex-row">
        {showWithin && (
          <ResultTable
            title="Within Species"
            rows={withinResults}
            showSpeciesColumn={false}
          />
        )}
        {showAcross && (
          <ResultTable
            title="Across Species"
            rows={acrossResults}
            showSpeciesColumn={true}
          />
        )}
      </div>
      <div className="flex gap-4 text-xs text-neutral-500">
        <span className="flex items-center gap-1.5">
          <span className="inline-block px-1.5 py-0.5 font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 rounded">OG</span>
          Same orthogroup as query (confirmed ortholog)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block px-1.5 py-0.5 font-medium bg-stone-50 text-stone-500 border border-stone-200 rounded">OG</span>
          Different orthogroup
        </span>
        <span className="flex items-center gap-1.5">
          <span className="text-neutral-300">--</span>
          No orthogroup data
        </span>
      </div>
    </div>
  );
}
