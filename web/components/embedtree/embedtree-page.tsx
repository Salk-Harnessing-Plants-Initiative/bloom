"use client";

import { useCallback, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { GenePicker, type GeneRow } from "./gene-picker";
import { KnnGraph, type KnnNeighbor } from "./knn-graph";
import { ResultsPanel } from "./results-panel";
import { DEFAULT_K, K_MAX, K_MIN, SIMILARITY_DISCLAIMER } from "./constants";

// Loose RPC + table types until web/lib/database.types.ts is regenerated to
// know about the embedtree RPCs and tables. See note in gene-picker.tsx.
type RpcCall = (name: string, args: object) => Promise<{
  data: unknown;
  error: { message?: string } | null;
}>;
type LooseTable = {
  select: (cols: string) => {
    in: (col: string, values: string[]) => Promise<{
      data: unknown;
      error: { message?: string } | null;
    }>;
  };
};
type LooseFrom = (table: string) => LooseTable;

// pgvector text format: "[1.234,5.678,...]"
function parsePgvector(s: string): number[] {
  return s.slice(1, -1).split(",").map(Number);
}

function ToggleChip({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-blue-500 bg-blue-50 text-blue-700"
          : "border-stone-300 bg-white text-neutral-500 hover:bg-stone-50"
      }`}
      aria-pressed={active}
    >
      <span
        className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
          active ? "bg-blue-500" : "bg-stone-300"
        }`}
        aria-hidden
      />
      {label}
    </button>
  );
}

type QueryState = {
  uid: string;
  species: string | null;
  gene_id: string | null;
};

/**
 * Top-level client component for /app/embedtree.
 *
 * Owns: selected query, K, in-flight KNN results, error state.
 * Children handle their own input (search) or render (graph).
 */
export function EmbedtreePage() {
  const supabase = createClientSupabaseClient();
  const [query, setQuery] = useState<QueryState | null>(null);
  const [k, setK] = useState<number>(DEFAULT_K);
  const [neighbors, setNeighbors] = useState<KnnNeighbor[]>([]);
  const [embeddings, setEmbeddings] = useState<Map<string, number[]>>(new Map());
  const [showQueryEdges, setShowQueryEdges] = useState(true);
  // Default off — query→neighbor edges are the primary signal; pairwise
  // edges are an optional second layer the user opts into.
  const [showPairwiseEdges, setShowPairwiseEdges] = useState(false);
  // Lifted hover state — graph and results table share it so hovering
  // one surface highlights the matching item in the other.
  const [hoveredUid, setHoveredUid] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runKnn = useCallback(
    async (q: QueryState, kVal: number) => {
      setLoading(true);
      setError(null);
      const { data, error: rpcErr } = await (supabase.rpc as unknown as RpcCall)(
        "knn_search_esm2",
        { query_uid: q.uid, match_count: kVal },
      );
      if (rpcErr) {
        setLoading(false);
        setError(rpcErr.message ?? String(rpcErr));
        setNeighbors([]);
        setEmbeddings(new Map());
        return;
      }

      const ns = ((data ?? []) as KnnNeighbor[]).filter((n) => n.uid !== q.uid);
      setNeighbors(ns);

      // Fetch the raw embedding vectors for query + neighbors so the graph
      // can draw pairwise inter-neighbor edges (the KNN RPC only returns
      // similarity to the query, not pairwise).
      const uids = [q.uid, ...ns.map((n) => n.uid)];
      const { data: embRows, error: embErr } = await (
        supabase.from as unknown as LooseFrom
      )("protein_embeddings_esm2")
        .select("uid, embedding")
        .in("uid", uids);
      setLoading(false);
      if (embErr) {
        setError(`embeddings fetch failed: ${embErr.message ?? String(embErr)}`);
        setEmbeddings(new Map());
        return;
      }
      const m = new Map<string, number[]>();
      for (const row of (embRows ?? []) as { uid: string; embedding: string | number[] }[]) {
        const vec = typeof row.embedding === "string"
          ? parsePgvector(row.embedding)
          : row.embedding;
        m.set(row.uid, vec);
      }
      setEmbeddings(m);
    },
    [supabase],
  );

  const handleSelect = useCallback(
    (row: GeneRow) => {
      const next: QueryState = {
        uid: row.uid,
        species: row.species,
        gene_id: row.gene_id,
      };
      setQuery(next);
      void runKnn(next, k);
    },
    [k, runKnn],
  );

  const handleKChange = useCallback(
    (next: number) => {
      const clamped = Math.max(K_MIN, Math.min(K_MAX, Math.round(next)));
      setK(clamped);
      if (query) void runKnn(query, clamped);
    },
    [query, runKnn],
  );

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-neutral-800">
          AI Orthologs
        </h1>
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <span className="font-semibold">Predicted, not verified.</span>{" "}
          {SIMILARITY_DISCLAIMER}
        </p>
      </header>

      <section className="flex flex-wrap items-center gap-3">
        <GenePicker onSelect={handleSelect} />
        <label className="flex items-center gap-2 text-sm text-neutral-700">
          K
          <input
            type="number"
            min={K_MIN}
            max={K_MAX}
            value={k}
            onChange={(e) => handleKChange(Number(e.target.value))}
            className="w-16 rounded-md border border-stone-300 bg-white px-2 py-1 text-sm shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          />
        </label>
        <ToggleChip
          label="Query edges"
          active={showQueryEdges}
          onToggle={() => setShowQueryEdges((v) => !v)}
        />
        <ToggleChip
          label="Pairwise edges"
          active={showPairwiseEdges}
          onToggle={() => setShowPairwiseEdges((v) => !v)}
        />
        {loading && (
          <span className="text-xs text-neutral-500">Running KNN…</span>
        )}
        {error && (
          <span className="text-xs text-red-600">Error: {error}</span>
        )}
      </section>

      <section className="flex flex-1 flex-col items-stretch gap-4 overflow-y-auto">
        {query ? (
          <>
            <div className="flex items-start justify-center">
              <KnnGraph
                queryUid={query.uid}
                querySpecies={query.species}
                queryGeneId={query.gene_id}
                neighbors={neighbors}
                embeddings={embeddings}
                showQueryEdges={showQueryEdges}
                showPairwiseEdges={showPairwiseEdges}
                onSelectNeighbor={(n) => handleSelect(n)}
                hoveredUid={hoveredUid}
                onHoverNode={setHoveredUid}
              />
            </div>
            <ResultsPanel
              queryUid={query.uid}
              querySpecies={query.species}
              queryGeneId={query.gene_id}
              neighbors={neighbors}
              onSelectNeighbor={(n) => handleSelect(n)}
              hoveredUid={hoveredUid}
              onHoverRow={setHoveredUid}
            />
          </>
        ) : (
          <div className="flex h-96 w-full max-w-3xl items-center justify-center self-center rounded-md border border-dashed border-stone-300 bg-stone-50 text-sm text-neutral-500">
            Search for a gene above to see its similarity neighborhood.
          </div>
        )}
      </section>
    </div>
  );
}
