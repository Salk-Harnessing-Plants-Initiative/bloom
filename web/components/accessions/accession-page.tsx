"use client";

import { useCallback, useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { AccessionGenePicker, type AccessionGeneRow } from "./accession-gene-picker";
import { AccessionRankingPanel } from "./accession-ranking-panel";
import { AccessionKnn } from "./accession-knn";
import { ACCESSION_DISCLAIMER, DEFAULT_K, K_MAX, K_MIN } from "./constants";

type Tab = "pergene" | "neighborhood";

// Loose table type until web/lib/database.types.ts is regenerated to know the
// accession tables.
type LooseFrom = (table: string) => {
  select: (cols: string) => any;
};

type Accession = { id: number; common_name: string };

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

const searchBtn =
  "rounded-md bg-blue-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Accessions tab body. Two surfaces, each with an autocomplete + explicit
 * Search button (nothing runs until Search is pressed):
 *   - Compare a gene (Surface A): search a gene → rank all accessions for it.
 *   - Find similar proteins (Surface B): pick an accession + a gene → that
 *     specific protein's nearest neighbors across the accession pan-proteome.
 */
export function AccessionPage() {
  const supabase = createClientSupabaseClient();
  const [tab, setTab] = useState<Tab>("pergene");

  // Surface A — `gene` is the picked suggestion; `submittedGene` is what the
  // panel actually queries (set on Search).
  const [gene, setGene] = useState<string | null>(null);
  const [submittedGene, setSubmittedGene] = useState<string | null>(null);

  // Surface B — accession + gene selections; `query` is committed on Search.
  const [accessions, setAccessions] = useState<Accession[]>([]);
  const [accId, setAccId] = useState<number | null>(null);
  const [nbhdGene, setNbhdGene] = useState<string | null>(null);
  const [k, setK] = useState(DEFAULT_K);
  const [query, setQuery] = useState<{ uid: string; label: string; k: number } | null>(null);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  // Load the accession list once for the Surface B dropdown.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { data } = await (supabase.from as unknown as LooseFrom)("arabidopsis_accessions")
        .select("id, common_name")
        .order("common_name");
      if (!cancelled) setAccessions((data ?? []) as Accession[]);
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase]);

  const accName = accessions.find((a) => a.id === accId)?.common_name ?? null;

  const handleGene = useCallback((row: AccessionGeneRow) => setGene(row.gene_id), []);
  const handleNbhdGene = useCallback((row: AccessionGeneRow) => setNbhdGene(row.gene_id), []);

  // Surface B: resolve (accession, gene) → protein uid, then commit the query.
  const findNeighbors = useCallback(async () => {
    if (accId == null || !nbhdGene) return;
    setResolving(true);
    setResolveMsg(null);
    const { data } = await (supabase.from as unknown as LooseFrom)("proteins")
      .select("uid")
      .eq("accession_id", accId)
      .eq("gene_id", nbhdGene)
      .limit(1);
    setResolving(false);
    const rows = (data ?? []) as { uid: string }[];
    if (rows.length === 0) {
      setQuery(null);
      setResolveMsg(`${accName ?? "This accession"} has no variant of ${nbhdGene}.`);
      return;
    }
    setQuery({ uid: rows[0].uid, label: `${nbhdGene} · ${accName ?? ""}`.trim(), k });
  }, [supabase, accId, nbhdGene, accName, k]);

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
          active={tab === "pergene"}
          label="Compare a gene across accessions"
          hint="How one gene's protein varies between accessions"
          info="Pick one gene. Every accession's version of that gene is ranked by how similar its protein is to the reference (Col-0). A score near 1.00 means nearly identical; lower means more divergent — so you can see which accessions vary most at that gene."
          onClick={() => setTab("pergene")}
        />
        <TabButton
          active={tab === "neighborhood"}
          label="Find similar proteins"
          hint="Nearest proteins across all accessions"
          info="Pick an accession and a gene to choose a specific protein. It then finds the most similar proteins across every gene and accession. The same gene in other accessions usually ranks near the top, followed by related genes."
          onClick={() => setTab("neighborhood")}
        />
      </section>

      {tab === "pergene" ? (
        <section className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-3">
            <AccessionGenePicker
              onSelect={handleGene}
              placeholder="Search a gene (e.g. AT1G01010)…"
              dedupeByGene
            />
            <button
              type="button"
              className={searchBtn}
              disabled={!gene}
              onClick={() => setSubmittedGene(gene)}
            >
              Search
            </button>
            <span className="text-xs text-neutral-500">
              Ranks every accession&apos;s variant of the gene vs. the reference (Col-0).
            </span>
          </div>
          {submittedGene ? (
            <AccessionRankingPanel geneId={submittedGene} />
          ) : (
            <EmptyState text="Search for a gene to rank its variants across accessions." />
          )}
        </section>
      ) : (
        <section className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-neutral-500">
              Accession
              <select
                value={accId ?? ""}
                onChange={(e) => setAccId(e.target.value ? Number(e.target.value) : null)}
                className="w-48 rounded-md border border-stone-300 bg-white px-2 py-1.5 text-sm text-neutral-800 shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              >
                <option value="">Select accession…</option>
                {accessions.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.common_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-neutral-500">
              Gene
              <AccessionGenePicker
                onSelect={handleNbhdGene}
                placeholder="Search a gene (e.g. AT1G01010)…"
                dedupeByGene
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-neutral-700">
              K
              <input
                type="number"
                min={K_MIN}
                max={K_MAX}
                value={k}
                onChange={(e) =>
                  setK(Math.max(K_MIN, Math.min(K_MAX, Math.round(Number(e.target.value)))))
                }
                className="w-16 rounded-md border border-stone-300 bg-white px-2 py-1 text-sm shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              />
            </label>
            <button
              type="button"
              className={searchBtn}
              disabled={accId == null || !nbhdGene || resolving}
              onClick={() => void findNeighbors()}
            >
              {resolving ? "Searching…" : "Search"}
            </button>
          </div>
          {resolveMsg && <p className="text-xs text-amber-700">{resolveMsg}</p>}
          {query ? (
            <AccessionKnn
              queryUid={query.uid}
              queryLabel={query.label}
              k={query.k}
              onSelectNeighbor={(n) => {
                setAccId(n.accession_id);
                setNbhdGene(n.gene_id);
                setQuery({
                  uid: n.uid,
                  label: `${n.gene_id ?? n.uid} · ${n.accession_name ?? ""}`.trim(),
                  k: query.k,
                });
              }}
            />
          ) : (
            !resolveMsg && (
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
