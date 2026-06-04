import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export interface DatabaseCounts {
  scrnaDatasets: number;
  cylExperiments: number;
  plateExperiments: number;
  traits: number;
  geneCandidates: number;
}

/**
 * Headline counts surfaced on the home page hero. Run in parallel so the
 * page render time is bound by the slowest single count, not the sum.
 */
export async function fetchDatabaseCounts(): Promise<DatabaseCounts> {
  const supabase = await createServerSupabaseClient();

  const [
    scrnaDatasets,
    cylExperiments,
    plateExperiments,
    traits,
    geneCandidates,
  ] = await Promise.all([
    countScrnaDatasets(supabase),
    countCylExperiments(supabase),
    countPlateExperiments(supabase),
    countTraits(supabase),
    countGeneCandidates(supabase),
  ]);

  return {
    scrnaDatasets,
    cylExperiments,
    plateExperiments,
    traits,
    geneCandidates,
  };
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

/** Sum of distinct trait definitions in the cyl and plate catalogues. */
async function countTraits(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
): Promise<number> {
  const [cyl, plate] = await Promise.all([
    supabase.from("cyl_traits").select("*", { count: "exact", head: true }),
    supabase
      .from("plate_plant_traits_list")
      .select("*", { count: "exact", head: true }),
  ]);
  return (cyl.count ?? 0) + (plate.count ?? 0);
}

async function countGeneCandidates(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
): Promise<number> {
  const { count } = await supabase
    .from("gene_candidates")
    .select("*", { count: "exact", head: true })
    .is("deleted_at", null);
  return count ?? 0;
}
