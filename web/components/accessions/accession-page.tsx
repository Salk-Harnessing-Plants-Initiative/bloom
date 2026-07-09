"use client";

import { useCallback, useState } from "react";
import { AccessionGenePicker, type AccessionGeneRow } from "./accession-gene-picker";
import { AccessionRankingPanel } from "./accession-ranking-panel";
import { AccessionKnn } from "./accession-knn";
import { ACCESSION_DISCLAIMER, DEFAULT_K, K_MAX, K_MIN } from "./constants";

type Tab = "pergene" | "neighborhood";

type ProteinSel = {
  uid: string;
  accession_id: number;
  accession_name: string | null;
  gene_id: string | null;
};

function TabButton({
  active,
  label,
  hint,
  onClick,
}: {
  active: boolean;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors ${
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
    </button>
  );
}

/**
 * Accessions tab body. Two surfaces, each with its OWN search:
 *   - Per-gene (Surface A): search a gene (locus-level) → rank all accessions
 *     for that gene against the reference (Col-0 by default).
 *   - Neighborhood (Surface B): search a specific accession protein → nearest
 *     neighbors across the accession pan-proteome.
 */
export function AccessionPage() {
  const [tab, setTab] = useState<Tab>("pergene");

  // Independent selections — the two searches don't share state.
  const [gene, setGene] = useState<string | null>(null);
  const [protein, setProtein] = useState<ProteinSel | null>(null);
  const [k, setK] = useState(DEFAULT_K);

  const handleGene = useCallback((row: AccessionGeneRow) => {
    setGene(row.gene_id);
  }, []);

  const handleProtein = useCallback((row: AccessionGeneRow) => {
    setProtein({
      uid: row.uid,
      accession_id: row.accession_id,
      accession_name: row.accession_name,
      gene_id: row.gene_id,
    });
  }, []);

  const proteinLabel = protein
    ? `${protein.gene_id ?? protein.uid} · ${protein.accession_name ?? ""}`.trim()
    : "";

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
          label="Per-gene"
          hint="Rank accessions for one gene"
          onClick={() => setTab("pergene")}
        />
        <TabButton
          active={tab === "neighborhood"}
          label="Neighborhood"
          hint="Nearest proteins across accessions"
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
            <span className="text-xs text-neutral-500">
              Ranks every accession&apos;s variant of the gene vs. the reference (Col-0).
            </span>
          </div>
          {gene ? (
            <AccessionRankingPanel geneId={gene} />
          ) : (
            <EmptyState text="Search for a gene to rank its variants across accessions." />
          )}
        </section>
      ) : (
        <section className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-3">
            <AccessionGenePicker
              onSelect={handleProtein}
              placeholder="Search an accession protein (gene · accession)…"
            />
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
          </div>
          {protein ? (
            <AccessionKnn
              queryUid={protein.uid}
              queryLabel={proteinLabel}
              k={k}
              onSelectNeighbor={(n) =>
                setProtein({
                  uid: n.uid,
                  accession_id: n.accession_id,
                  accession_name: n.accession_name,
                  gene_id: n.gene_id,
                })
              }
            />
          ) : (
            <EmptyState text="Search for an accession protein to see its nearest neighbors." />
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
