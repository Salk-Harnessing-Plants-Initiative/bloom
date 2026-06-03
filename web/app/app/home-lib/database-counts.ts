import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export interface DatabaseCounts {
  scrnaDatasets: number;
  cylExperiments: number;
  plateExperiments: number;
}

/**
 * Headline counts surfaced on the home page hero. Run in parallel so the
 * page render time is bound by the slowest single count, not the sum.
 */
export async function fetchDatabaseCounts(): Promise<DatabaseCounts> {
  const supabase = await createServerSupabaseClient();

  const [scrnaDatasets, cylExperiments, plateExperiments] = await Promise.all([
    countScrnaDatasets(supabase),
    countCylExperiments(supabase),
    countPlateExperiments(supabase),
  ]);

  return { scrnaDatasets, cylExperiments, plateExperiments };
}

async function countScrnaDatasets(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
): Promise<number> {
  const { count } = await supabase
    .from("scrna_datasets")
    .select("*", { count: "exact", head: true })
    .is("deleted_at", null)
    .neq("name", "NULL_DATASET");
  return count ?? 0;
}

async function countCylExperiments(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
): Promise<number> {
  const { count } = await supabase
    .from("cyl_experiments")
    .select("*", { count: "exact", head: true })
    .is("deleted_at", null);
  return count ?? 0;
}

async function countPlateExperiments(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
): Promise<number> {
  // gravi_experiments isn't in the generated typings yet; cast through unknown
  // to keep this file self-contained without touching the typings.
  const { count } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_experiments")
    .select("*", { count: "exact", head: true });
  return count ?? 0;
}
