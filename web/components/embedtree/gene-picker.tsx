"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { speciesColor } from "./constants";

const DEBOUNCE_MS = 300;
const PAGE_SIZE = 20;

// Loose RPC type until web/lib/database.types.ts is regenerated to know about
// the embedtree RPCs (`search_genes`, `knn_search_esm2`). Drop this when the
// generated types catch up.
type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;

export type GeneRow = {
  uid: string;
  species: string | null;
  gene_id: string | null;
};

type Props = {
  onSelect: (row: GeneRow) => void;
};

/**
 * Debounced autocomplete over the `search_genes(partial, max_results)` RPC.
 *
 * Race-condition handling: every request is tagged with a sequence number
 * and only the latest response renders, so a slow request that resolves
 * after a faster later one doesn't overwrite the fresh dropdown.
 */
export function GenePicker({ onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<GeneRow[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);

  const supabase = createClientSupabaseClient();
  const seqRef = useRef(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback(
    async (partial: string, seq: number) => {
      setLoading(true);
      // Cast: search_genes isn't yet in the generated Database types.
      // Regenerate web/lib/database.types.ts after embedtree migrations
      // land on prod and the cast goes away.
      const { data, error } = await (supabase.rpc as unknown as RpcCall)(
        "search_genes",
        { partial_id: partial, max_results: PAGE_SIZE },
      );
      // Stale response — a newer request was fired after this one.
      if (seq !== seqRef.current) return;
      setLoading(false);
      if (error) {
        setRows([]);
        return;
      }
      setRows((data ?? []) as GeneRow[]);
      setHoverIdx(0);
    },
    [supabase],
  );

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      setRows([]);
      setOpen(false);
      return;
    }
    setOpen(true);
    const seq = ++seqRef.current;
    debounceRef.current = setTimeout(() => runSearch(trimmed, seq), DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, runSearch]);

  function handleKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || rows.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHoverIdx((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHoverIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(rows[hoverIdx]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  function pick(row: GeneRow) {
    onSelect(row);
    setQuery(row.gene_id ?? row.uid);
    setOpen(false);
  }

  return (
    <div className="relative w-full max-w-xl">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKey}
        onFocus={() => query.trim() && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Search by gene id (e.g. AT5G16970)…"
        className="w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
      />
      {loading && (
        <div className="absolute right-3 top-2.5 text-xs text-neutral-400">
          …
        </div>
      )}
      {open && rows.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-y-auto rounded-md border border-stone-200 bg-white shadow-lg">
          {rows.map((r, i) => (
            <li
              key={r.uid}
              onMouseDown={() => pick(r)}
              onMouseEnter={() => setHoverIdx(i)}
              className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                i === hoverIdx ? "bg-blue-50" : "bg-white"
              }`}
            >
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: speciesColor(r.species) }}
                aria-hidden
              />
              <span className="font-mono text-neutral-800">{r.gene_id ?? r.uid}</span>
              <span className="ml-auto text-xs text-neutral-500">
                {r.species ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && !loading && rows.length === 0 && query.trim().length > 0 && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-stone-200 bg-white px-3 py-2 text-sm text-neutral-500 shadow-sm">
          No matching genes.
        </div>
      )}
    </div>
  );
}
