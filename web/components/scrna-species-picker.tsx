"use client";

import { useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  addSpecies,
  normalizeNewSpecies,
  type SpeciesOption,
} from "@/lib/species-options";

// The select's value for "add a species", which no species id can equal.
const ADD_NEW = "add-new";

type AddStatus = "closed" | "open" | "saving";

export default function ScrnaSpeciesPicker({
  species,
  value,
  onChange,
  fieldClass,
  labelClass,
}: {
  species: SpeciesOption[];
  value: number | null;
  onChange: (id: number | null) => void;
  fieldClass: string;
  labelClass: string;
}) {
  const [options, setOptions] = useState(species);
  const [addStatus, setAddStatus] = useState<AddStatus>("closed");
  const [genus, setGenus] = useState("");
  const [epithet, setEpithet] = useState("");
  const [commonName, setCommonName] = useState("");
  const [problem, setProblem] = useState("");
  const [notice, setNotice] = useState("");

  const saving = addStatus === "saving";
  const typed = Boolean(genus.trim() || epithet.trim() || commonName.trim());
  const preview = normalizeNewSpecies({ genus, species: epithet, commonName });

  // A failed add's message goes once the name is edited.
  function edit(set: (value: string) => void, value: string) {
    setProblem("");
    set(value);
  }

  function choose(selected: string) {
    setNotice("");
    if (selected === ADD_NEW) {
      setAddStatus("open");
      onChange(null);
      return;
    }
    setAddStatus("closed");
    onChange(selected ? Number(selected) : null);
  }

  async function add() {
    if ("problem" in preview) return;
    setProblem("");
    setAddStatus("saving");
    const result = await addSpecies(createClientSupabaseClient(), preview.value);
    if (result.kind === "error") {
      setProblem(result.message);
      setAddStatus("open");
      return;
    }
    const option = result.option;
    setOptions((current) =>
      current.some((o) => o.id === option.id)
        ? current
        : [...current, option].sort((a, b) => a.label.localeCompare(b.label))
    );
    setNotice(
      result.kind === "added"
        ? `Added ${option.label}.`
        : `${option.label} is already in Bloom, so it's selected.`
    );
    setGenus("");
    setEpithet("");
    setCommonName("");
    setAddStatus("closed");
    onChange(option.id);
  }

  return (
    <div>
      <label className={labelClass}>
        Species
        <select
          className={fieldClass}
          value={addStatus === "closed" ? (value ?? "") : ADD_NEW}
          onChange={(e) => choose(e.target.value)}
          disabled={saving}
        >
          <option value="">Choose a species</option>
          {options.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
          <option value={ADD_NEW}>+ Add a species…</option>
        </select>
      </label>

      {addStatus === "closed" ? (
        notice ? (
          <p role="status" className="mt-1 text-sm text-stone-500">
            {notice}
          </p>
        ) : null
      ) : (
        <div className="mt-3 space-y-3 rounded-md border border-stone-200 bg-white p-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <label className={labelClass}>
              Genus
              <input
                className={fieldClass}
                value={genus}
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
              ), for everyone, and shown on the Expression page.
            </p>
          ) : typed ? (
            <p aria-live="polite" className="text-sm text-stone-500">
              {preview.problem}
            </p>
          ) : (
            <p className="text-sm text-stone-500">
              Added to Bloom&apos;s species list for everyone, and shown on the
              Expression page.
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
            <button
              type="button"
              onClick={() => choose("")}
              disabled={saving}
              className="text-sm text-stone-500 hover:text-stone-800"
            >
              Cancel
            </button>
            {problem ? (
              <p role="alert" className="text-sm text-red-700">
                {problem}
              </p>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
