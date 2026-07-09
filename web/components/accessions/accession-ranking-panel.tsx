"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { accessionColor, NOISE_EPSILON } from "./constants";
import { downloadCsv, toCsv } from "./csv";

type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;

export type AccessionRankRow = {
  uid: string;
  accession_id: number;
  accession_name: string | null;
  gene_id: string | null;
  similarity: number;
  is_reference: boolean;
};

type Props = {
  geneId: string;
  // Omit to let the RPC default the reference to the designated (Col-0)
  // accession; pass a uid to rank against a specific accession's variant.
  referenceUid?: string;
};

/**
 * Surface A — per-gene across accessions. For a fixed gene_id, ranks each
 * accession's protein variant by ESM-3 cosine similarity to the reference
 * (the selected protein). Backed by `compare_gene_across_accessions`.
 */
export function AccessionRankingPanel({ geneId, referenceUid }: Props) {
  const supabase = createClientSupabaseClient();
  const [rows, setRows] = useState<AccessionRankRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      const { data, error: rpcErr } = await (supabase.rpc as unknown as RpcCall)(
        "compare_gene_across_accessions",
        { target_gene_id: geneId, reference_uid: referenceUid ?? null, match_count: 1000 },
      );
      if (cancelled) return;
      setLoading(false);
      if (rpcErr) {
        setError(rpcErr.message ?? String(rpcErr));
        setRows([]);
        return;
      }
      setRows((data ?? []) as AccessionRankRow[]);
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase, geneId, referenceUid ?? null]);

  // Ordering is within noise if every similarity sits within EPSILON of the top.
  const withinNoise =
    rows.length > 1 &&
    rows.every((r) => Math.abs(rows[0].similarity - r.similarity) <= NOISE_EPSILON);

  const handleDownload = () => {
    const csv = toCsv(
      ["rank", "accession_name", "accession_id", "uid", "gene_id", "cosine_similarity", "is_reference"],
      rows.map((r, i) => [
        r.is_reference ? "" : i,
        r.accession_name,
        r.accession_id,
        r.uid,
        r.gene_id,
        r.similarity.toFixed(6),
        r.is_reference,
      ]),
    );
    downloadCsv(`${geneId}_accession_ranking.csv`, csv);
  };

  return (
    <div className="rounded-md border border-stone-200 bg-white">
      <div className="flex items-center justify-between border-b border-stone-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {geneId} across accessions
        </span>
        <div className="flex items-center gap-3">
          {loading && <span className="text-xs text-neutral-500">Loading…</span>}
          <button
            type="button"
            onClick={handleDownload}
            disabled={rows.length === 0}
            className="rounded-md border border-stone-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-700 shadow-sm transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Download CSV
          </button>
        </div>
      </div>

      {error && <p className="px-3 py-2 text-xs text-red-600">Error: {error}</p>}

      {withinNoise && (
        <p className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
          All variants are within {NOISE_EPSILON} cosine of each other — the ordering
          here is within noise, not a confident ranking.
        </p>
      )}

      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="w-12 px-3 py-2 text-right font-medium">Rank</th>
              <th className="px-3 py-2 text-left font-medium">Accession</th>
              <th className="w-40 px-3 py-2 text-right font-medium">Cosine similarity</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.uid}
                className={`border-t border-stone-100 ${
                  row.is_reference ? "bg-blue-50/50" : ""
                }`}
              >
                <td className="px-3 py-1.5 text-right font-mono text-xs text-neutral-400">
                  {row.is_reference ? "—" : i}
                </td>
                <td className="px-3 py-1.5">
                  <span className="flex items-center gap-2">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: accessionColor(row.accession_id) }}
                      aria-hidden
                    />
                    <span className="text-neutral-800">{row.accession_name}</span>
                    {row.is_reference && (
                      <span className="rounded-full bg-blue-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">
                        Reference
                      </span>
                    )}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                  {row.similarity.toFixed(4)}
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && !error && (
              <tr>
                <td colSpan={3} className="px-3 py-6 text-center text-sm text-neutral-500">
                  No accession variants with an ESM-3 embedding for {geneId}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
