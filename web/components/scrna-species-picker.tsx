"use client";

import Link from "next/link";
import { useState } from "react";
import type { SpeciesOption } from "@/lib/species-options";
import SpeciesAddForm, { type SpeciesAdded } from "@/components/species-add-form";

// The select's value for "add a species", which no species id can equal.
const ADD_NEW = "add-new";

type AddStatus = "closed" | "open" | "saving";

export default function ScrnaSpeciesPicker({
  species,
  value,
  onChange,
  onAdded,
  fieldClass,
  labelClass,
}: {
  species: SpeciesOption[];
  value: number | null;
  onChange: (id: number | null) => void;
  // A species added here, for the caller to keep in its list.
  onAdded: (option: SpeciesOption) => void;
  fieldClass: string;
  labelClass: string;
}) {
  const [addStatus, setAddStatus] = useState<AddStatus>("closed");
  const [notice, setNotice] = useState("");
  // The species chosen before "Add a species", restored on Cancel.
  const [previous, setPrevious] = useState<number | null>(null);

  const saving = addStatus === "saving";

  function choose(selected: string) {
    setNotice("");
    if (selected === ADD_NEW) {
      setPrevious(value);
      setAddStatus("open");
      onChange(null);
      return;
    }
    setAddStatus("closed");
    onChange(selected ? Number(selected) : null);
  }

  function cancelAdd() {
    setAddStatus("closed");
    onChange(previous);
  }

  function added({ kind, option }: SpeciesAdded) {
    onAdded(option);
    setNotice(
      kind === "added"
        ? `Added ${option.label}.`
        : `${option.label} is already in Bloom, so it's selected.`
    );
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
          {species.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
          <option value={ADD_NEW}>+ Add a species…</option>
        </select>
      </label>
      {/* A new tab, so a half-filled job form isn't lost. */}
      <Link
        href="/app/species"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-1 inline-block text-xs text-stone-500 hover:text-lime-700 hover:underline"
      >
        See all species
      </Link>

      {addStatus === "closed" ? (
        notice ? (
          <p role="status" className="mt-1 text-sm text-stone-500">
            {notice}
          </p>
        ) : null
      ) : (
        <div className="mt-3">
          <SpeciesAddForm
            onAdded={added}
            onCancel={cancelAdd}
            onSavingChange={(s) => setAddStatus(s ? "saving" : "open")}
            autoFocus
            fieldClass={fieldClass}
            labelClass={labelClass}
          />
        </div>
      )}
    </div>
  );
}
