"use client";

/**
 * The Species page's list and its Add a species form. The list is the server's, with each
 * species' image; adding one reloads it from the server. Species aren't renamed or removed
 * here: uploads find a species by its common name.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { addedByText, type SpeciesListItem } from "@/lib/species-options";
import SpeciesAddForm, { type SpeciesAdded } from "@/components/species-add-form";

const FIELD_CLASS =
  "mt-1 block w-full rounded-md border border-stone-300 px-2 py-1.5 text-sm text-stone-900";
const LABEL_CLASS = "block text-sm font-medium text-stone-700";

/** `species` is the server's list, already sorted; `currentUserId` is who's signed in. */
export default function SpeciesCatalog({
  species: list,
  currentUserId,
}: {
  species: SpeciesListItem[];
  currentUserId: string | null;
}) {
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");
  // Back to the Add a species button when the form closes, so keyboard users keep their place.
  const addButton = useRef<HTMLButtonElement>(null);
  const wasAdding = useRef(false);
  useEffect(() => {
    if (wasAdding.current && !adding) addButton.current?.focus();
    wasAdding.current = adding;
  }, [adding]);

  function added({ kind, option }: SpeciesAdded) {
    // A new species, or one already in Bloom but added since the page loaded: reload the list.
    if (kind === "added" || !list.some((s) => s.id === option.id)) router.refresh();
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
            autoFocus
            fieldClass={FIELD_CLASS}
            labelClass={LABEL_CLASS}
          />
        ) : (
          <button
            ref={addButton}
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
        {/* Always mounted, so screen readers announce a new notice. */}
        <p role="status" className="mt-2 min-h-5 text-sm text-stone-600">
          {adding ? "" : notice}
        </p>
      </div>

      <div>
        <div className="mb-2 text-sm text-stone-500">{list.length} species in Bloom</div>
        {list.length === 0 ? (
          <p className="text-sm text-stone-500">No species yet.</p>
        ) : (
          <table className="w-full border-b border-t-2 border-b-stone-200 border-t-stone-600 text-left text-sm" aria-label="Species">
            <thead>
              <tr className="border-b-2 border-stone-600 text-xs uppercase tracking-wide text-lime-800">
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Species
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Added by
                </th>
                <th scope="col" className="py-2 font-semibold">
                  Image
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-200">
              {list.map((s) => (
                <tr key={s.id}>
                  <td className="py-2 pr-4 text-stone-800">{s.label}</td>
                  <td className="py-2 pr-4 text-stone-500" title={s.addedBy ?? undefined}>
                    {addedByText(s.addedBy, currentUserId)}
                  </td>
                  <td className="py-2">
                    <span className="flex items-center gap-2 text-xs text-stone-500">
                      {/* eslint-disable-next-line @next/next/no-img-element -- a small local or signed image, like Illustration's */}
                      <img
                        src={s.image.url}
                        alt=""
                        className="h-8 w-8 object-contain"
                      />
                      {s.image.placeholder ? "No image yet" : null}
                    </span>
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
