import "server-only";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export type SpeciesMontageRow = {
  id: number;
  common_name: string | null;
  illustration_path: string | null;
};

/**
 * Fetch the top-N species (by scRNA dataset count) for the hero montage.
 * Falls back to alphabetical order when no datasets are attached.
 */
export async function fetchHomeSpeciesMontage(
  limit = 5,
): Promise<SpeciesMontageRow[]> {
  const supabase = await createServerSupabaseClient();
  const { data } = await supabase
    .from("species")
    .select("id, common_name, illustration_path, scrna_datasets(id)")
    .is("deleted_at", null);
  if (!data) return [];
  const rows = data as Array<
    SpeciesMontageRow & { scrna_datasets: { id: number }[] | null }
  >;
  rows.sort((a, b) => {
    const ac = a.scrna_datasets?.length ?? 0;
    const bc = b.scrna_datasets?.length ?? 0;
    if (bc !== ac) return bc - ac;
    return (a.common_name ?? "").localeCompare(b.common_name ?? "");
  });
  return rows.slice(0, limit).map(({ id, common_name, illustration_path }) => ({
    id,
    common_name,
    illustration_path,
  }));
}

/** Total species count for the headline. */
export async function fetchSpeciesCount(): Promise<number> {
  const supabase = await createServerSupabaseClient();
  const { count } = await supabase
    .from("species")
    .select("*", { count: "exact", head: true })
    .is("deleted_at", null);
  return count ?? 0;
}
