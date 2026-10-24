"use client";

/**
 * The Species page's list and its Add a species form. The list stays live through
 * Realtime: a species added or edited anywhere in Bloom shows up without a reload. A
 * removed one disappears on the next reload or rejoin, since row-level security keeps
 * removed species (and so their change events) from signed-in users; admins, who can
 * read them, see it go straight away. A species added here, or found already in Bloom,
 * is listed straight away too. Species aren't renamed or removed here: uploads find a
 * species by its common name.
 */

import { useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import {
  applySpeciesChange,
  sortedSpeciesOptions,
  withSpeciesOption,
  type SpeciesOption,
} from "@/lib/species-options";
import SpeciesAddForm, { type SpeciesAdded } from "@/components/species-add-form";

const FIELD_CLASS =
  "mt-1 block w-full rounded-md border border-stone-300 px-2 py-1.5 text-sm text-stone-900";
const LABEL_CLASS = "block text-sm font-medium text-stone-700";

/** The species that haven't been removed, as the list shows them. */
async function loadSpecies(): Promise<SpeciesOption[]> {
  const { data, error } = await createClientSupabaseClient()
    .from("species")
    .select("id, common_name, genus, species")
    .is("deleted_at", null);
  if (error) throw new Error(error.message);
  return sortedSpeciesOptions(data ?? []);
}

/** `species` is the server's snapshot, already sorted. */
export default function SpeciesCatalog({ species }: { species: SpeciesOption[] }) {
  const live = useLiveSync<SpeciesOption[]>({
    topic: "species",
    bindings: [{ table: "species", event: "*" }],
    initial: species,
    snapshot: loadSpecies,
    apply: (list, change) => applySpeciesChange(list, change.new as Parameters<typeof applySpeciesChange>[1]),
  });
  const list = live.view;
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");

  function added({ kind, option }: SpeciesAdded) {
    // Listed either way: one "already in Bloom" may have been added since the page loaded.
    live.update((current) => withSpeciesOption(current, option));
    setNotice(kind === "added" ? `Added ${option.label}.` : `${option.label} is already in Bloom.`);
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
        {live.error ? (
          <p role="alert" className="mb-2 text-sm text-red-700">
            Couldn&apos;t refresh the species: {live.error}
          </p>
        ) : null}
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
