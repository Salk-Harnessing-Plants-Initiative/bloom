"use client";

import { AccessionGenePicker, type AccessionGeneRow } from "./accession-gene-picker";
import { K_MAX, K_MIN } from "./constants";

export type Accession = { id: number; common_name: string };

const searchBtn =
  "rounded-md bg-blue-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50";

type Props = {
  accessions: Accession[];
  accId: number | null;
  onAccIdChange: (id: number | null) => void;
  geneText: string;
  onGeneTextChange: (t: string) => void;
  onGeneSelect: (row: AccessionGeneRow) => void;
  k: number;
  onKChange: (k: number) => void;
  onSearch: () => void;
  searchDisabled: boolean;
  searching: boolean;
  accName: string | null;
  // Show the K numeric input, and its label/bounds. Best-match uses it for
  // nearest-per-accession (1–10); the neighbourhood tab for K neighbours (5–50).
  showK?: boolean;
  kLabel?: string;
  kMin?: number;
  kMax?: number;
};

/**
 * Shared query-protein input for both accession surfaces: pick an accession,
 * then type-ahead its genes (scoped so the pair can't mismatch), set K, Search.
 * Fully controlled — the parent owns state so it can pivot to a clicked result.
 */
export function AccessionProteinPicker({
  accessions,
  accId,
  onAccIdChange,
  geneText,
  onGeneTextChange,
  onGeneSelect,
  k,
  onKChange,
  onSearch,
  searchDisabled,
  searching,
  accName,
  showK = true,
  kLabel = "K",
  kMin = K_MIN,
  kMax = K_MAX,
}: Props) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-neutral-500">
        Accession
        <select
          value={accId ?? ""}
          onChange={(e) => onAccIdChange(e.target.value ? Number(e.target.value) : null)}
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
          value={geneText}
          onValueChange={onGeneTextChange}
          onSelect={onGeneSelect}
          placeholder={
            accId != null
              ? `Click to browse or search ${accName ?? "this accession"}'s genes…`
              : "Select an accession, then browse its genes…"
          }
          accessionId={accId}
          disabled={accId == null}
          dedupeByGene
        />
      </label>
      {showK && (
        <label className="flex items-center gap-2 text-sm text-neutral-700">
          {kLabel}
          <input
            type="number"
            min={kMin}
            max={kMax}
            value={k}
            onChange={(e) =>
              onKChange(Math.max(kMin, Math.min(kMax, Math.round(Number(e.target.value)))))
            }
            className="w-16 rounded-md border border-stone-300 bg-white px-2 py-1 text-sm shadow-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          />
        </label>
      )}
      <button
        type="button"
        className={searchBtn}
        disabled={searchDisabled}
        onClick={onSearch}
      >
        {searching ? "Searching…" : "Search"}
      </button>
    </div>
  );
}
