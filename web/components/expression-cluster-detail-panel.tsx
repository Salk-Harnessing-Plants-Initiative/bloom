"use client";

import { useEffect, useState } from "react";
import {
  fetchClusterStats,
  fetchDeExports,
  type DeExport,
  type ClusterStatsRow,
} from "@/components/expression-lib/cluster-markers";

export interface ExpressionClusterDetailPanelProps {
  datasetId: number;
  clusterId: string;
  clusterName: string | null;
  clusterColor: string | null;
}

/**
 * Right-hand drawer describing the currently soloed cluster.
 * Mounted by the cockpit only when exactly one cluster is visible so the
 * UMAP canvas keeps its full width when nothing is soloed.
 */
/** "pFACT_vs_Col-0" -> "pFACT vs Col-0", for a link label. */
function humanContrast(contrast: string): string {
  return contrast.replace(/_vs_/g, " vs ");
}

export function ExpressionClusterDetailPanel({
  datasetId,
  clusterId,
  clusterName,
  clusterColor,
}: ExpressionClusterDetailPanelProps) {
  const [stats, setStats] = useState<ClusterStatsRow | null>(null);
  const [deExports, setDeExports] = useState<DeExport[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchClusterStats(datasetId, clusterId),
      fetchDeExports(datasetId, clusterId),
    ]).then(([s, exports]) => {
      if (cancelled) return;
      setStats(s);
      setDeExports(exports);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [datasetId, clusterId]);

  const name = clusterName ?? clusterId;
  const markers = stats?.markers ?? null;
  // pct is stored as a percentage (0..100) per the column name; render directly.
  const pctHuman =
    stats?.pct != null ? stats.pct.toFixed(1) : "—";
  const cellsHuman =
    stats?.cell_count != null
      ? new Intl.NumberFormat("en-US").format(stats.cell_count)
      : "—";

  return (
    <aside className="w-80 shrink-0 border-l border-stone-200 bg-white overflow-y-auto">
      <div className="p-5 border-b border-stone-200">
        <div className="flex items-center gap-2 mb-3">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: clusterColor ?? "#65a30d" }}
          />
          <span className="text-xs uppercase tracking-widest text-stone-500">
            Cluster
          </span>
        </div>
        <h2
          className="text-2xl font-serif italic text-stone-900 truncate"
          title={name}
        >
          {name}
        </h2>
        <div className="mt-1 text-sm text-stone-500">
          cluster_id {clusterId} · {cellsHuman} cells
        </div>
      </div>

      <div className="grid grid-cols-2 border-b border-stone-200">
        <StatTile label="% of dataset" value={`${pctHuman}%`} suffix={false} />
        <StatTile
          label="DE genes Q<.01"
          value={markers ? String(markers.n_significant) : "—"}
          borderLeft
        />
      </div>

      <div className="p-5 border-b border-stone-200">
        <div className="flex items-baseline gap-2 mb-3">
          <span className="text-xs uppercase tracking-widest text-stone-500">
            Top markers
          </span>
          <span className="text-[10px] text-stone-400">(scrna_de)</span>
        </div>

        {loading ? (
          <div className="text-xs italic text-stone-400">Loading…</div>
        ) : !markers || markers.top.length === 0 ? (
          <div className="text-xs italic text-stone-400">
            No markers yet — waiting on DE ingest.
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] uppercase tracking-widest text-stone-500">
                <th className="text-left font-normal pb-2">Gene</th>
                <th className="text-right font-normal pb-2">Log₂FC</th>
                <th className="text-right font-normal pb-2">Q</th>
                <th className="text-right font-normal pb-2">Pct.1</th>
              </tr>
            </thead>
            <tbody>
              {markers.top.map((m) => (
                <tr
                  key={m.gene}
                  className="border-b border-dashed border-stone-200/70 last:border-0"
                >
                  <td className="py-2 font-mono text-stone-800">{m.gene}</td>
                  <td className="py-2 text-right tabular-nums text-lime-700 font-semibold">
                    {m.log2fc.toFixed(2)}
                  </td>
                  <td className="py-2 text-right tabular-nums text-stone-500">
                    {m.q.toExponential(0)}
                  </td>
                  <td className="py-2 text-right tabular-nums text-stone-600">
                    {m.pct_1.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="p-5 flex gap-4 text-xs">
        <button
          type="button"
          className="text-lime-700 hover:underline"
          onClick={() => {
            /* Rename — placeholder, follow-up PR wires the real modal */
          }}
        >
          Rename
        </button>
        <span className="text-stone-300">·</span>
        {deExports.length === 0 ? (
          <span className="text-stone-400 cursor-not-allowed" title="No DE CSV yet">
            Export CSV
          </span>
        ) : (
          deExports.map((de, i) => (
            <span key={de.filePath}>
              {i > 0 && <span className="text-stone-300"> · </span>}
              <a
                href={de.filePath}
                target="_blank"
                rel="noopener"
                className="text-lime-700 hover:underline"
                title={
                  de.contrast
                    ? `${name} cells, ${humanContrast(de.contrast)}`
                    : `${name} against all other cells`
                }
              >
                {de.contrast ? humanContrast(de.contrast) : "Export CSV"}
              </a>
            </span>
          ))
        )}
        <span className="text-stone-300">·</span>
        <button
          type="button"
          className="text-lime-700 hover:underline"
          onClick={() => {
            /* Run DE vs. all — placeholder */
          }}
        >
          Run DE vs. all
        </button>
      </div>
    </aside>
  );
}

function StatTile({
  label,
  value,
  borderLeft = false,
  suffix = true,
}: {
  label: string;
  value: string;
  borderLeft?: boolean;
  suffix?: boolean;
}) {
  const percentMatch = suffix ? value.match(/^(.*?)(%)$/) : null;
  const head = percentMatch ? percentMatch[1] : value;
  const tail = percentMatch ? percentMatch[2] : null;
  return (
    <div
      className={[
        "p-5",
        borderLeft ? "border-l border-stone-200" : "",
      ].join(" ")}
    >
      <div className="text-[10px] uppercase tracking-widest text-stone-500 mb-2">
        {label}
      </div>
      <div className="text-2xl font-semibold text-stone-900 tabular-nums">
        {head}
        {tail ? (
          <span className="ml-0.5 text-sm text-stone-500 font-normal">
            {tail}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default ExpressionClusterDetailPanel;
