"use client";

/**
 * The fields for adding a species (genus, species name, common name), with a preview of
 * how it will be stored. Used by the job form's species picker and the Species page.
 * Adding goes through add_species, which returns the species already stored under the
 * same name instead of a duplicate.
 */

import { useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  addSpecies,
  normalizeNewSpecies,
  type SpeciesOption,
} from "@/lib/species-options";

export type SpeciesAdded = { kind: "added" | "existing"; option: SpeciesOption };

export default function SpeciesAddForm({
  onAdded,
  onCancel,
  onSavingChange,
  autoFocus = false,
  fieldClass,
  labelClass,
}: {
  // The species added, or the one already in Bloom under that name.
  onAdded: (result: SpeciesAdded) => void;
  // Shown as a Cancel button when given.
  onCancel?: () => void;
  onSavingChange?: (saving: boolean) => void;
  // Put the cursor in Genus when the form opens.
  autoFocus?: boolean;
  fieldClass: string;
  labelClass: string;
}) {
  const [genus, setGenus] = useState("");
  const [epithet, setEpithet] = useState("");
  const [commonName, setCommonName] = useState("");
  const [problem, setProblem] = useState("");
  const [saving, setSaving] = useState(false);

  const typed = Boolean(genus.trim() || epithet.trim() || commonName.trim());
  const preview = normalizeNewSpecies({ genus, species: epithet, commonName });

  // A failed add's message goes once the name is edited.
  function edit(set: (value: string) => void, value: string) {
    setProblem("");
    set(value);
  }

  function setSavingState(value: boolean) {
    setSaving(value);
    onSavingChange?.(value);
  }

  async function add() {
    if ("problem" in preview) return;
    setProblem("");
    setSavingState(true);
    const result = await addSpecies(createClientSupabaseClient(), preview.value);
    setSavingState(false);
    if (result.kind === "error") {
      setProblem(result.message);
      return;
    }
    setGenus("");
    setEpithet("");
    setCommonName("");
    onAdded(result);
  }

  return (
    <div className="space-y-3 rounded-md border border-stone-200 bg-white p-4">
      <div className="grid max-w-md gap-3">
        <label className={labelClass}>
          Genus
          <input
            className={fieldClass}
            value={genus}
            autoFocus={autoFocus}
            onChange={(e) => edit(setGenus, e.target.value)}
            placeholder="e.g. Arabidopsis"
            disabled={saving}
          />
        </label>
        <label className={labelClass}>
          Species name
          <input
            className={fieldClass}
            value={epithet}
            onChange={(e) => edit(setEpithet, e.target.value)}
            placeholder="e.g. thaliana"
            disabled={saving}
          />
        </label>
        <label className={labelClass}>
          Common name
          <input
            className={fieldClass}
            value={commonName}
            onChange={(e) => edit(setCommonName, e.target.value)}
            placeholder="e.g. Arabidopsis"
            disabled={saving}
          />
        </label>
      </div>
      {"value" in preview ? (
        <p aria-live="polite" className="text-sm text-stone-700">
          Will be added as{" "}
          <span className="font-medium">{preview.value.common_name}</span>{" "}
          (<span className="italic">
            {preview.value.genus} {preview.value.species}
          </span>
          ), for everyone to use.
        </p>
      ) : typed ? (
        <p aria-live="polite" className="text-sm text-stone-500">
          {preview.problem}
        </p>
      ) : (
        <p className="text-sm text-stone-500">
          Added to Bloom&apos;s species list for everyone to use.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={add}
          disabled={saving || "problem" in preview}
          className="rounded-md border border-lime-700 px-3 py-1.5 text-sm font-medium text-lime-800 hover:bg-lime-50 disabled:opacity-50"
        >
          {saving ? "Adding…" : "Add species"}
        </button>
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="text-sm text-stone-500 hover:text-stone-800"
          >
            Cancel
          </button>
        ) : null}
        {problem ? (
          <p role="alert" className="text-sm text-red-700">
            {problem}
          </p>
        ) : null}
      </div>
    </div>
  );
}
