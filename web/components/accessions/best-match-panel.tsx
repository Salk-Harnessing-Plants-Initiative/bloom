"use client";

import { useEffect, useMemo, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { accessionColor } from "./constants";
import { downloadCsv, toCsv } from "./csv";
import {
  similarityRank,
  sortBestMatchRows,
  type BestMatchRow,
  type SortDir,
  type SortKey,
} from "./best-match-sort";

export type { BestMatchRow } from "./best-match-sort";

type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;

type Props = {
  queryUid: string;
  // Human label for the query protein, shown as the reference.
  queryLabel: string;
  k: number;
  onSelectMatch?: (row: BestMatchRow) => void;
};

/**
 * Best match per accession. For the query protein, each OTHER accession's most
 * similar protein by ESM-3 cosine, ranked, top-K. Backed by
 * `best_match_per_accession`. The query protein is the reference.
 */
export function BestMatchPanel({ queryUid, queryLabel, k, onSelectMatch }: Props) {
  const supabase = createClientSupabaseClient();
  const [rows, setRows] = useState<BestMatchRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("similarity");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "similarity" ? "desc" : "asc"); // most-similar / A→Z first
    }
  };

  // Similarity rank (1 = most similar), stable regardless of the display sort.
  const rankByUid = useMemo(() => similarityRank(rows), [rows]);
  const sorted = useMemo(() => sortBestMatchRows(rows, sortKey, sortDir), [rows, sortKey, sortDir]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      const { data, error: rpcErr } = await (supabase.rpc as unknown as RpcCall)(
        "best_match_per_accession",
        { query_uid: queryUid, match_count: k },
      );
      if (cancelled) return;
      setLoading(false);
      if (rpcErr) {
        setError(rpcErr.message ?? String(rpcErr));
        setRows([]);
        return;
      }
      setRows((data ?? []) as BestMatchRow[]);
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase, queryUid, k]);

  const handleDownload = () => {
    const csv = toCsv(
      ["rank", "accession_name", "accession_id", "matched_uid", "matched_gene_id", "cosine_similarity"],
      rows.map((r, i) => [
        i + 1,
        r.accession_name,
        r.accession_id,
        r.uid,
        r.gene_id,
        r.similarity.toFixed(6),
      ]),
    );
    downloadCsv(`${queryLabel.replace(/[^\w.-]+/g, "_")}_best_match_per_accession.csv`, csv);
  };

  return (
    <div className="rounded-md border border-stone-200 bg-white">
      <div className="flex items-center justify-between border-b border-stone-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Closest protein per accession
          <span className="ml-2 normal-case font-normal tracking-normal text-neutral-400">
            {rows.length > 0 ? `${rows.length} accessions · ` : ""}vs. {queryLabel}
          </span>
        </span>
        <div className="flex items-center gap-3">
          {loading && <span className="text-xs text-neutral-500">Loading…</span>}
          <button
            type="button"
            disabled
            title="Protein sequences are not loaded yet"
            className="cursor-not-allowed rounded-md border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs font-medium text-neutral-400"
          >
            Download FASTA · coming soon
          </button>
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

      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
            <tr>
              <th className="w-12 px-3 py-2 text-right font-medium">Rank</th>
              {([
                ["accession_name", "Accession", "text-left"],
                ["gene_id", "Closest gene", "text-left"],
                ["similarity", "Cosine similarity", "w-44 text-right"],
              ] as [SortKey, string, string][]).map(([col, label, cls]) => (
                <th
                  key={col}
                  onClick={() => toggleSort(col)}
                  aria-sort={
                    sortKey === col ? (sortDir === "asc" ? "ascending" : "descending") : "none"
                  }
                  className={`${cls} cursor-pointer select-none px-3 py-2 font-medium hover:text-neutral-700`}
                >
                  {label}{" "}
                  <span className="text-[10px]">
                    {sortKey === col ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr
                key={row.uid}
                onClick={onSelectMatch ? () => onSelectMatch(row) : undefined}
                className={`border-t border-stone-100 ${
                  onSelectMatch ? "cursor-pointer hover:bg-stone-50" : ""
                }`}
              >
                <td className="px-3 py-1.5 text-right font-mono text-xs text-neutral-400">
                  {rankByUid.get(row.uid)}
                </td>
                <td className="px-3 py-1.5">
                  <span className="flex items-center gap-2">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: accessionColor(row.accession_id) }}
                      aria-hidden
                    />
                    <span className="text-neutral-800">{row.accession_name}</span>
                  </span>
                </td>
                <td className="px-3 py-1.5 font-mono text-xs text-neutral-600">{row.gene_id}</td>
                <td className="px-3 py-1.5 text-right font-mono text-neutral-700">
                  {row.similarity.toFixed(4)}
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && !error && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-sm text-neutral-500">
                  No matches found for {queryLabel}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
