"use server";

/**
 * Adding a species, from the Species page or the scRNA job form. It runs on the server so that
 * a species that's new to Bloom also sends an email, so its image can be added (see
 * lib/species-added-email.ts). The email goes out after the response, so nobody waits for it.
 */

import { after } from "next/server";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import {
  addSpecies,
  normalizeNewSpecies,
  type AddSpeciesResult,
  type NewSpeciesInput,
} from "@/lib/species-options";
import { notifySpeciesAdded } from "@/lib/species-added-email";

export async function addSpeciesAction(input: NewSpeciesInput): Promise<AddSpeciesResult> {
  // Checked again here: the browser's check can be skipped.
  const checked = normalizeNewSpecies(input);
  if ("problem" in checked) return { kind: "error", message: checked.problem };
  const result = await addSpecies(await createServerSupabaseClient(), checked.value);
  if (result.kind === "added") {
    const user = await getUser();
    after(() => notifySpeciesAdded({ label: result.option.label, addedBy: user?.email ?? null }));
  }
  return result;
}
