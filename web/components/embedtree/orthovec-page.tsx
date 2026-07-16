"use client";

import { useState } from "react";
import { EmbedtreePage } from "./embedtree-page";
import { AccessionPage } from "@/components/accessions/accession-page";

type Tab = "crossspecies" | "accessions";

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
      className={`rounded-t-md border-b-2 px-3 py-2 text-left transition-colors ${
        active
          ? "border-blue-500 text-blue-700"
          : "border-transparent text-neutral-500 hover:text-neutral-700"
      }`}
      aria-pressed={active}
    >
      <span className="block text-sm font-medium">{label}</span>
      <span className="block text-xs text-neutral-400">{hint}</span>
    </button>
  );
}

/**
 * OrthoVec — protein-embedding similarity tools. Two surfaces:
 *   - Cross-species: predicted orthologs across plant species (ESM-2).
 *   - Arabidopsis accessions: single-species accession comparison (ESM-3).
 */
export function OrthoVecPage() {
  const [tab, setTab] = useState<Tab>("crossspecies");

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-stone-200 px-4 pt-4">
        <div>
          <h1 className="text-xl font-semibold text-neutral-800">OrthoVec</h1>
          <p className="text-sm text-neutral-500">
            Predicted orthologs and accession variants from protein-embedding
            similarity.
          </p>
        </div>
        <nav className="flex gap-1" aria-label="OrthoVec surfaces">
          <TabButton
            active={tab === "crossspecies"}
            label="Cross-species"
            hint="Orthologs across species · ESM-2"
            onClick={() => setTab("crossspecies")}
          />
          <TabButton
            active={tab === "accessions"}
            label="Arabidopsis accessions"
            hint="Single-species variants · ESM-3"
            onClick={() => setTab("accessions")}
          />
        </nav>
      </div>

      <div className="min-h-0 flex-1">
        {tab === "crossspecies" ? <EmbedtreePage /> : <AccessionPage />}
      </div>
    </div>
  );
}
