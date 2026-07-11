"use client";

import { useCallback, useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { type AccessionGeneRow } from "./accession-gene-picker";
import { AccessionProteinPicker, type Accession } from "./accession-protein-picker";
import { BestMatchPanel } from "./best-match-panel";
import { AccessionKnn } from "./accession-knn";
import {
  ACCESSION_DISCLAIMER,
  DEFAULT_K,
  PER_ACCESSION_DEFAULT,
  PER_ACCESSION_MAX,
  PER_ACCESSION_MIN,
} from "./constants";

type Tab = "bestmatch" | "neighborhood";

// Loose PostgREST builder type until web/lib/database.types.ts is regenerated
// to know the accession tables. Chain methods return the builder; awaiting it
// yields { data, error } (no `any`).
type LooseResult = { data: unknown; error: { message?: string } | null };
interface LooseQuery extends PromiseLike<LooseResult> {
  select: (cols: string) => LooseQuery;
  order: (col: string) => LooseQuery;
  eq: (col: string, val: unknown) => LooseQuery;
  limit: (n: number) => LooseQuery;
}
type LooseFrom = (table: string) => LooseQuery;

type Pivot = {
  uid: string;
  accession_id: number;
  gene_id: string | null;
  accession_name: string | null;
};

function InfoDot({ text }: { text: string }) {
  return (
    <span className="group/info absolute right-2 top-2">
      <span
        tabIndex={0}
        role="img"
        aria-label={text}
        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-stone-300 text-[10px] font-semibold italic text-neutral-400 outline-none focus:ring-1 focus:ring-blue-500"
      >
        i
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-6 z-20 w-64 rounded-md border border-stone-200 bg-white px-3 py-2 text-xs font-normal leading-snug text-neutral-600 opacity-0 shadow-lg transition-opacity group-hover/info:opacity-100 group-focus-within/info:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

function TabButton({
  active,
  label,
  hint,
  info,
  onClick,
}: {
  active: boolean;
  label: string;
  hint: string;
  info: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative flex flex-col items-start rounded-md border px-3 py-2 pr-8 text-left transition-colors ${
        active
          ? "border-blue-500 bg-blue-50"
          : "border-stone-300 bg-white hover:bg-stone-50"
      }`}
      aria-pressed={active}
    >
      <span className={`text-sm font-medium ${active ? "text-blue-700" : "text-neutral-700"}`}>
        {label}
      </span>
      <span className="text-xs text-neutral-500">{hint}</span>
      <InfoDot text={info} />
    </button>
  );
}

/**
 * Query-protein state shared by both surfaces: an accession + a gene within it
 * resolve to one protein uid, which each surface's panel then queries. Kept in a
 * hook so both tabs get identical pick / resolve / pivot behaviour.
 */
function useProteinQuery(accessions: Accession[], initialK: number = DEFAULT_K) {
  const supabase = createClientSupabaseClient();
  const [accId, setAccId] = useState<number | null>(null);
  const [geneText, setGeneText] = useState("");
  const [gene, setGene] = useState<string | null>(null);
  const [k, setK] = useState(initialK);
  const [query, setQuery] = useState<{ uid: string; label: string; k: number } | null>(null);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null); // benign "no variant"
  const [searchError, setSearchError] = useState<string | null>(null); // actual failure
  const [resolving, setResolving] = useState(false);

  const accName = accessions.find((a) => a.id === accId)?.common_name ?? null;

  // Changing the accession invalidates the gene (genes are per-accession).
  const onAccIdChange = useCallback((id: number | null) => {
    setAccId(id);
    setGene(null);
    setGeneText("");
  }, []);

  const onGeneTextChange = useCallback((t: string) => {
    setGeneText(t);
    setGene(null); // typing invalidates the committed selection
  }, []);

  // A Helixer gene_id belongs to exactly one accession, so the picked gene
  // fully determines its accession — sync it so the pair can't mismatch.
  const onGeneSelect = useCallback((row: AccessionGeneRow) => {
    setGene(row.gene_id);
    setAccId(row.accession_id);
  }, []);

  const search = useCallback(async () => {
    if (accId == null || !gene) return;
    setResolving(true);
    setResolveMsg(null);
    setSearchError(null);
    // limit(1) is safe: UNIQUE (accession_id, gene_id) allows at most one row.
    const { data, error } = await (supabase.from as unknown as LooseFrom)("proteins")
      .select("uid")
      .eq("accession_id", accId)
      .eq("gene_id", gene)
      .limit(1);
    setResolving(false);
    if (error) {
      setQuery(null);
      setSearchError(error.message ?? "Lookup failed. Please try again.");
      return;
    }
    const rows = (data ?? []) as { uid: string }[];
    if (rows.length === 0) {
      setQuery(null);
      setResolveMsg(`${accName ?? "This accession"} has no variant of ${gene}.`);
      return;
    }
    setQuery({ uid: rows[0].uid, label: `${gene} · ${accName ?? ""}`.trim(), k });
  }, [supabase, accId, gene, accName, k]);

  // Re-query from a clicked result so every input and the panel stay in sync.
  const pivot = useCallback((next: Pivot) => {
    setAccId(next.accession_id);
    setGene(next.gene_id);
    setGeneText(next.gene_id ?? "");
    setResolveMsg(null);
    setSearchError(null);
    setQuery((q) => ({
      uid: next.uid,
      label: `${next.gene_id ?? next.uid} · ${next.accession_name ?? ""}`.trim(),
      k: q?.k ?? k,
    }));
  }, [k]);

  return {
    accId, geneText, gene, k, setK, query, resolveMsg, searchError, resolving,
    accName, onAccIdChange, onGeneTextChange, onGeneSelect, search, pivot,
  };
}

/**
 * Accessions tab body. Two surfaces, each pinning one query protein (accession +
 * scoped gene) then Search:
 *   - Best match per accession: the closest protein to that gene in each other
 *     accession, ranked by ESM-3 cosine (embedding comparison — needed because
 *     Helixer gene IDs aren't shared across accessions).
 *   - Find similar proteins: that protein's nearest neighbours across the whole
 *     accession pan-proteome.
 */
export function AccessionPage() {
  const supabase = createClientSupabaseClient();
  const [tab, setTab] = useState<Tab>("bestmatch");
  const [accessions, setAccessions] = useState<Accession[]>([]);
  const [accListError, setAccListError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { data, error } = await (supabase.from as unknown as LooseFrom)("arabidopsis_accessions")
        .select("id, common_name")
        .order("common_name");
      if (cancelled) return;
      if (error) {
        setAccListError(error.message ?? "Failed to load accessions.");
        return;
      }
      setAccListError(null);
      setAccessions((data ?? []) as Accession[]);
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase]);

  // Best-match shows every accession (sortable table); neighbourhood uses a K.
  const bm = useProteinQuery(accessions, PER_ACCESSION_DEFAULT);
  const nb = useProteinQuery(accessions);

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <header className="flex flex-col gap-1">
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <span className="font-semibold">Predicted, not verified.</span>{" "}
          {ACCESSION_DISCLAIMER}
        </p>
      </header>

      <section className="flex gap-3">
        <TabButton
          active={tab === "bestmatch"}
          label="Best match per accession"
          hint="Your gene's closest match in each accession"
          info="Pick a protein (accession + gene). Retrieves the proteins nearest your query in ESM-3 embedding space and shows each accession's closest one — the likely counterpart of your gene — by cosine similarity. Fast approximate (nearest-neighbour) search over the closest matches, so it surfaces the accessions nearest your gene, not necessarily all ~458. 'Per accession' returns the top-N."
          onClick={() => setTab("bestmatch")}
        />
        <TabButton
          active={tab === "neighborhood"}
          label="Find similar proteins"
          hint="Most similar proteins, anywhere in the panel"
          info="Pick a protein (accession + gene). Nearest-neighbour retrieval over ESM-3 embeddings — ranks every accession protein by cosine similarity to your query, no sequence alignment."
          onClick={() => setTab("neighborhood")}
        />
      </section>

      {accListError && (
        <p className="text-xs text-red-600">Couldn&apos;t load accessions: {accListError}</p>
      )}

      {tab === "bestmatch" ? (
        <section className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <AccessionProteinPicker
            accessions={accessions}
            accId={bm.accId}
            onAccIdChange={bm.onAccIdChange}
            geneText={bm.geneText}
            onGeneTextChange={bm.onGeneTextChange}
            onGeneSelect={bm.onGeneSelect}
            k={bm.k}
            onKChange={bm.setK}
            onSearch={() => void bm.search()}
            searchDisabled={bm.accId == null || !bm.gene || bm.resolving}
            searching={bm.resolving}
            accName={bm.accName}
            kLabel="Per accession"
            kMin={PER_ACCESSION_MIN}
            kMax={PER_ACCESSION_MAX}
          />
          <span className="text-xs text-neutral-500">
            Each accession&apos;s closest protein to your gene (among your query&apos;s nearest
            matches), most similar first. Click a column to sort.
          </span>
          {bm.searchError && <p className="text-xs text-red-600">{bm.searchError}</p>}
          {bm.resolveMsg && <p className="text-xs text-amber-700">{bm.resolveMsg}</p>}
          {bm.query ? (
            <BestMatchPanel
              queryUid={bm.query.uid}
              queryLabel={bm.query.label}
              perAccession={bm.query.k}
              onSelectMatch={(r) =>
                bm.pivot({
                  uid: r.uid,
                  accession_id: r.accession_id,
                  gene_id: r.gene_id,
                  accession_name: r.accession_name,
                })
              }
            />
          ) : (
            !bm.resolveMsg && (
              <EmptyState text="Pick an accession and a gene, then press Search." />
            )
          )}
        </section>
      ) : (
        <section className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <AccessionProteinPicker
            accessions={accessions}
            accId={nb.accId}
            onAccIdChange={nb.onAccIdChange}
            geneText={nb.geneText}
            onGeneTextChange={nb.onGeneTextChange}
            onGeneSelect={nb.onGeneSelect}
            k={nb.k}
            onKChange={nb.setK}
            onSearch={() => void nb.search()}
            searchDisabled={nb.accId == null || !nb.gene || nb.resolving}
            searching={nb.resolving}
            accName={nb.accName}
          />
          {nb.searchError && <p className="text-xs text-red-600">{nb.searchError}</p>}
          {nb.resolveMsg && <p className="text-xs text-amber-700">{nb.resolveMsg}</p>}
          {nb.query ? (
            <AccessionKnn
              queryUid={nb.query.uid}
              queryLabel={nb.query.label}
              k={nb.query.k}
              onSelectNeighbor={(n) =>
                nb.pivot({
                  uid: n.uid,
                  accession_id: n.accession_id,
                  gene_id: n.gene_id,
                  accession_name: n.accession_name,
                })
              }
            />
          ) : (
            !nb.resolveMsg && (
              <EmptyState text="Pick an accession and a gene, then press Search." />
            )
          )}
        </section>
      )}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex h-72 w-full max-w-3xl items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 text-sm text-neutral-500">
      {text}
    </div>
  );
}
