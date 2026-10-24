"use client";

/**
 * The Species page's list and its Add a species form. A species added here joins the
 * list; one already in Bloom is reported, not listed twice. Species aren't renamed or
 * removed here: uploads find a species by its common name.
 */

import { useState } from "react";
import type { SpeciesOption } from "@/lib/species-options";
import SpeciesAddForm, { type SpeciesAdded } from "@/components/species-add-form";

const FIELD_CLASS =
  "mt-1 block w-full rounded-md border border-stone-300 px-2 py-1.5 text-sm text-stone-900";
const LABEL_CLASS = "block text-sm font-medium text-stone-700";

function byLabel(a: SpeciesOption, b: SpeciesOption) {
  return a.label.localeCompare(b.label);
}

export default function SpeciesCatalog({ species }: { species: SpeciesOption[] }) {
  const [list, setList] = useState(() => [...species].sort(byLabel));
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");

  function added({ kind, option }: SpeciesAdded) {
    if (kind === "added") {
      setList((current) =>
        current.some((s) => s.id === option.id) ? current : [...current, option].sort(byLabel)
      );
      setNotice(`Added ${option.label}.`);
    } else {
      setNotice(`${option.label} is already in Bloom.`);
    }
    setAdding(false);
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        {adding ? (
          <SpeciesAddForm
            onAdded={added}
            onCancel={() => setAdding(false)}
            fieldClass={FIELD_CLASS}
            labelClass={LABEL_CLASS}
          />
        ) : (
          <button
            type="button"
            onClick={() => {
              setNotice("");
              setAdding(true);
            }}
            className="rounded-md border border-lime-700 px-3 py-1.5 text-sm font-medium text-lime-800 hover:bg-lime-50"
          >
            Add a species
          </button>
        )}
        {notice && !adding ? (
          <p role="status" className="mt-2 text-sm text-stone-600">
            {notice}
          </p>
        ) : null}
      </div>

      <div>
        <div className="mb-2 text-sm text-stone-500">
          {list.length} species in Bloom
        </div>
        {list.length === 0 ? (
          <p className="text-sm text-stone-500">No species yet.</p>
        ) : (
          <ul className="divide-y divide-stone-200 border-y border-stone-200" aria-label="Species">
            {list.map((s) => (
              <li key={s.id} className="py-2 text-sm text-stone-800">
                {s.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
