"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { accessionColor } from "./constants";
import { downloadCsv, toCsv } from "./csv";

type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;

export type AccessionNeighbor = {
  uid: string;
  accession_id: number;
  accession_name: string | null;
  gene_id: string | null;
  similarity: number;
};

type Props = {
  queryUid: string;
  queryLabel: string;
  k: number;
  onSelectNeighbor: (neighbor: AccessionNeighbor) => void;
};

/**
 * Surface B — pan-proteome nearest neighbors across accession proteins,
 * backed by `knn_search_esm3`. Each neighbor shows its gene_id and accession,
 * colored by accession. A ranked list (not the cross-species force-directed
 * graph, which is hard-wired to species and reused via a follow-up).
 */
export function AccessionKnn({ queryUid, queryLabel, k, onSelectNeighbor }: Props) {
  const supabase = createClientSupabaseClient();
  const [neighbors, setNeighbors] = useState<AccessionNeighbor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      const { data, error: rpcErr } = await (supabase.rpc as unknown as RpcCall)(
        "knn_search_esm3",
        { query_uid: queryUid, match_count: k },
      );
      if (cancelled) return;
      setLoading(false);
      if (rpcErr) {
        setError(rpcErr.message ?? String(rpcErr));
        setNeighbors([]);
        return;
      }
      // Drop the query's own row (self-match, similarity 1.0).
      setNeighbors(((data ?? []) as AccessionNeighbor[]).filter((n) => n.uid !== queryUid));
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase, queryUid, k]);

  const handleDownload = () => {
    const csv = toCsv(
      ["rank", "gene_id", "accession_name", "accession_id", "uid", "cosine_similarity"],
      neighbors.map((n, i) => [
        i + 1,
        n.gene_id,
        n.accession_name,
        n.accession_id,
        n.uid,
        n.similarity.toFixed(6),
      ]),
    );
    downloadCsv(`${queryUid.replace(/[^\w.-]+/g, "_")}_neighbors.csv`, csv);
  };

  return (
    <div className="rounded-md border border-stone-200 bg-white">
      <div className="flex items-center justify-between border-b border-stone-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Nearest neighbors of {queryLabel}
        </span>
        <div className="flex items-center gap-3">
          {loading && <span className="text-xs text-neutral-500">Running KNN…</span>}
          <button
            type="button"
            onClick={handleDownload}
            disabled={neighbors.length === 0}
            className="rounded-md border border-stone-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-700 shadow-sm transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Download CSV
          </button>
        </div>
      </div>

      {error && <p className="px-3 py-2 text-xs text-red-600">Error: {error}</p>}

      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="w-12 px-3 py-2 text-right font-medium">Rank</th>
              <th className="px-3 py-2 text-left font-medium">Gene</th>
              <th className="px-3 py-2 text-left font-medium">Accession</th>
              <th className="w-40 px-3 py-2 text-right font-medium">Cosine similarity</th>
            </tr>
          </thead>
          <tbody>
            {neighbors.map((n, i) => (
              <tr
                key={n.uid}
                onClick={() => onSelectNeighbor(n)}
                className="cursor-pointer border-t border-stone-100 hover:bg-stone-50"
              >
                <td className="px-3 py-1.5 text-right font-mono text-xs text-neutral-400">
                  {i + 1}
                </td>
                <td className="px-3 py-1.5 font-mono text-neutral-800">{n.gene_id ?? n.uid}</td>
                <td className="px-3 py-1.5">
                  <span className="flex items-center gap-2">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: accessionColor(n.accession_id) }}
                      aria-hidden
                    />
                    <span className="text-neutral-700">{n.accession_name}</span>
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                  {n.similarity.toFixed(4)}
                </td>
              </tr>
            ))}
            {!loading && neighbors.length === 0 && !error && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-sm text-neutral-500">
                  No neighbors found for this protein.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
