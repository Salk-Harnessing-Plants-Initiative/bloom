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
  addedByText,
  applySpeciesChange,
  sortedSpeciesList,
  withSpeciesOption,
  type SpeciesListItem,
} from "@/lib/species-options";
import SpeciesAddForm, { type SpeciesAdded } from "@/components/species-add-form";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";

const FIELD_CLASS =
  "mt-1 block w-full rounded-md border border-stone-300 px-2 py-1.5 text-sm text-stone-900";
const LABEL_CLASS = "block text-sm font-medium text-stone-700";

/** The species that haven't been removed, as the list shows them. */
async function loadSpecies(): Promise<SpeciesListItem[]> {
  const { data, error } = await createClientSupabaseClient()
    .from("species")
    .select("id, common_name, genus, species, created_by")
    .is("deleted_at", null);
  if (error) throw new Error(error.message);
  return sortedSpeciesList(data ?? []);
}

/** `species` is the server's snapshot, already sorted; `currentUserId` is who's signed in. */
export default function SpeciesCatalog({
  species,
  currentUserId,
}: {
  species: SpeciesListItem[];
  currentUserId: string | null;
}) {
  const live = useLiveSync<SpeciesListItem[]>({
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
    if (kind === "added") {
      live.update((current) => withSpeciesOption(current, { ...option, addedBy: currentUserId }));
    } else if (!live.current().some((s) => s.id === option.id)) {
      // Already in Bloom but not listed: added since the page loaded. Reload the list to
      // show it with who added it.
      live.refresh();
    }
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
        <div className="mb-2 flex items-center justify-between gap-4 text-sm text-stone-500">
          <span>{list.length} species in Bloom</span>
          <LiveIndicator state={live.connection} onRefresh={live.refresh} />
        </div>
        {live.error ? (
          <p role="alert" className="mb-2 text-sm text-red-700">
            Couldn&apos;t refresh the species: {live.error}
          </p>
        ) : null}
        {list.length === 0 ? (
          <p className="text-sm text-stone-500">No species yet.</p>
        ) : (
          <table className="w-full border-y border-lime-300 text-left text-sm" aria-label="Species">
            <thead>
              <tr className="border-b border-lime-300 text-xs uppercase tracking-wide text-lime-800">
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Species
                </th>
                <th scope="col" className="py-2 font-semibold">
                  Added by
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-lime-200">
              {list.map((s) => (
                <tr key={s.id}>
                  <td className="py-2 pr-4 text-stone-800">{s.label}</td>
                  <td className="py-2 text-stone-500" title={s.addedBy ?? undefined}>
                    {addedByText(s.addedBy, currentUserId)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
