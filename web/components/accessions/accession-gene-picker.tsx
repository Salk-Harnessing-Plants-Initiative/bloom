"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { accessionColor } from "./constants";

const DEBOUNCE_MS = 300;
const PAGE_SIZE = 20;

// Loose RPC type until web/lib/database.types.ts is regenerated to know about
// the accession RPCs (search_accession_genes). Drop when types catch up.
type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;

export type AccessionGeneRow = {
  uid: string;
  accession_id: number;
  accession_name: string | null;
  gene_id: string | null;
};

type Props = {
  onSelect: (row: AccessionGeneRow) => void;
  placeholder?: string;
  // Gene-level search: collapse the per-accession rows to one row per gene_id
  // (Per-gene surface picks a locus, not a specific accession's protein).
  dedupeByGene?: boolean;
};

/**
 * Debounced autocomplete over `search_accession_genes(partial, max_results)`.
 *
 * Scoped to accession proteins only. Every request is tagged with a sequence
 * number so a slow response can't overwrite a fresher one. With `dedupeByGene`
 * the dropdown shows one entry per gene_id (locus-level); otherwise it shows
 * each accession's protein separately (protein-level).
 */
export function AccessionGenePicker({
  onSelect,
  placeholder = "Search accession gene (e.g. AT1G01010)…",
  dedupeByGene = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<AccessionGeneRow[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);

  const supabase = createClientSupabaseClient();
  const seqRef = useRef(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback(
    async (partial: string, seq: number) => {
      setLoading(true);
      const { data, error } = await (supabase.rpc as unknown as RpcCall)(
        "search_accession_genes",
        { partial_id: partial, max_results: PAGE_SIZE },
      );
      if (seq !== seqRef.current) return; // stale
      setLoading(false);
      if (error) {
        setRows([]);
        return;
      }
      let next = (data ?? []) as AccessionGeneRow[];
      if (dedupeByGene) {
        const seen = new Set<string>();
        next = next.filter((r) => {
          const key = r.gene_id ?? r.uid;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      }
      setRows(next);
      setHoverIdx(0);
    },
    [supabase, dedupeByGene],
  );

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();
    if (trimmed.length < 1) {
      setRows([]);
      return;
    }
    const seq = ++seqRef.current;
    debounceRef.current = setTimeout(() => void runSearch(trimmed, seq), DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, runSearch]);

  const choose = useCallback(
    (row: AccessionGeneRow) => {
      setQuery(
        dedupeByGene
          ? (row.gene_id ?? row.uid)
          : `${row.gene_id ?? row.uid} · ${row.accession_name ?? ""}`.trim(),
      );
      setOpen(false);
      onSelect(row);
    },
    [onSelect, dedupeByGene],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || rows.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHoverIdx((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHoverIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(rows[hoverIdx]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="relative w-96">
      <input
        type="text"
        value={query}
        placeholder={placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="w-full rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
      />
      {loading && (
        <span className="absolute right-3 top-2 text-xs text-neutral-400">…</span>
      )}
      {open && rows.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-stone-200 bg-white shadow-lg">
          {rows.map((row, i) => (
            <li
              key={row.uid}
              onMouseEnter={() => setHoverIdx(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(row);
              }}
              className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${
                i === hoverIdx ? "bg-blue-50" : ""
              }`}
            >
              {!dedupeByGene && (
                <span
                  className="inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: accessionColor(row.accession_id) }}
                  aria-hidden
                />
              )}
              <span className="font-mono text-neutral-800">{row.gene_id ?? row.uid}</span>
              {!dedupeByGene && (
                <span className="ml-auto text-xs text-neutral-500">
                  {row.accession_name}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
