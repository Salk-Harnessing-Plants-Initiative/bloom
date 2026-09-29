/**
 * Species as the job form lists them, and adding a new one with the add_species function,
 * which returns the species already stored under the same genus and species, or the same
 * common name (compared in lower case), instead of a duplicate.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

export type SpeciesOption = { id: number; label: string };

type SpeciesRow = Pick<
  Database["public"]["Tables"]["species"]["Row"],
  "id" | "common_name" | "genus" | "species"
>;

export type NewSpeciesInput = { genus: string; species: string; commonName: string };

export type NewSpecies = { genus: string; species: string; common_name: string };

export type AddSpeciesResult =
  | { kind: "added"; option: SpeciesOption }
  | { kind: "existing"; option: SpeciesOption }
  | { kind: "error"; message: string };

const COMMON_NAME_MAX = 100;

// What add_species raises, mapped to what the form says.
const RPC_ERRORS: Record<string, string> = {
  "23505": "A removed species has that name. Ask a Bloom admin.",
  "42501": "Sign in to add a species.",
  "22023": "Check the genus, species and common name.",
};

/** "Arabidopsis (Arabidopsis thaliana)", from however the row is cased. */
export function speciesOption(row: SpeciesRow): SpeciesOption {
  const genus = row.genus
    ? row.genus[0].toUpperCase() + row.genus.slice(1).toLowerCase()
    : "";
  const scientific = [genus, row.species?.toLowerCase()].filter(Boolean).join(" ");
  const common = row.common_name ?? scientific;
  return {
    id: row.id,
    label: scientific && scientific !== common ? `${common} (${scientific})` : common,
  };
}

export function sortedSpeciesOptions(rows: SpeciesRow[]): SpeciesOption[] {
  return rows.map(speciesOption).sort((a, b) => a.label.localeCompare(b.label));
}

/** The species as add_species stores it (Genus, epithet, Common name), or why not. */
export function normalizeNewSpecies(
  input: NewSpeciesInput
): { value: NewSpecies } | { problem: string } {
  const genus = input.genus.trim();
  const species = input.species.trim().toLowerCase();
  const commonName = input.commonName.trim().replace(/\s+/g, " ");
  if (!/^[A-Za-z]+$/.test(genus)) return { problem: "The genus is one word of letters." };
  if (!/^[a-z]+(-[a-z]+)*$/.test(species)) {
    return { problem: "The species is one word of letters, e.g. thaliana." };
  }
  if (!commonName) return { problem: "Enter a common name." };
  if (commonName.length > COMMON_NAME_MAX) {
    return { problem: `The common name is at most ${COMMON_NAME_MAX} characters.` };
  }
  return {
    value: {
      genus: genus[0].toUpperCase() + genus.slice(1).toLowerCase(),
      species,
      common_name: commonName[0].toUpperCase() + commonName.slice(1),
    },
  };
}

/** Adds the species, or returns the one already stored under the same name. */
export async function addSpecies(
  supabase: SupabaseClient<Database>,
  value: NewSpecies
): Promise<AddSpeciesResult> {
  const { data, error } = await supabase
    .rpc("add_species", {
      p_genus: value.genus,
      p_species: value.species,
      p_common_name: value.common_name,
    })
    .single();
  if (data) {
    return { kind: data.result === "added" ? "added" : "existing", option: speciesOption(data) };
  }
  const known = error?.code ? RPC_ERRORS[error.code] : undefined;
  return { kind: "error", message: known ?? "Couldn't add the species. Try again shortly." };
}
