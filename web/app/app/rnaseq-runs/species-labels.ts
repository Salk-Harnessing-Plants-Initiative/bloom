import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";
import { speciesOption } from "@/lib/species-options";

/** Every species' label by id, e.g. 1 → "Arabidopsis (Arabidopsis thaliana)". */
export async function speciesLabelMap(
  supabase: SupabaseClient<Database>
): Promise<Record<number, string>> {
  const { data } = await supabase
    .from("species")
    .select("id, common_name, genus, species")
    .is("deleted_at", null);
  return Object.fromEntries((data ?? []).map((row) => [row.id, speciesOption(row).label]));
}
