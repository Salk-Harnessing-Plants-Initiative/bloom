import "server-only";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** Total species count for the headline. */
export async function fetchSpeciesCount(): Promise<number> {
  const supabase = await createServerSupabaseClient();
  const { count } = await supabase
    .from("species")
    .select("*", { count: "exact", head: true })
    .is("deleted_at", null);
  return count ?? 0;
}
