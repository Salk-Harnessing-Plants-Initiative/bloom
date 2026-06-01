/**
 * Query helper for the "Recent phenotypes by cylinder scanner" home-page
 * widget. Reads from the `recent_experiments_by_cyl_scanner` Postgres view
 * (defined in `supabase/migrations/20260528120100_*.sql`).
 *
 * (View name still says "experiments" — kept stable because the migration is
 * already committed. UI-facing copy uses "phenotypes" for parallelism with the
 * plate-scanner widget.)
 *
 * The view already does the major joins:
 *   - Joins cyl_scans → cyl_plants → cyl_waves → cyl_experiments → species
 *     and phenotypers
 *   - Picks the most-recent-upload row per (scanner, wave) pair
 *   - Ranks those pairs within each scanner and filters to the top 2
 *   - Respects RLS (security_invoker = true) so soft-deleted experiments
 *     and rows the calling role can't read are excluded
 *
 * This helper just fetches all view rows and groups them per scanner so the
 * UI can render one section per scanner with up to 2 cards each.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

/**
 * One row from `recent_experiments_by_cyl_scanner` — represents one
 * (scanner, experiment, wave) tuple ranked in the top 2 for its scanner.
 */
export interface CylScanRow {
  scanner_id: number;
  scanner_name: string | null;
  experiment_id: number;
  experiment_name: string;
  species_id: number | null;
  species_common_name: string | null;
  wave_id: number;
  wave_number: number | null;
  wave_name: string | null;
  plant_age_days: number | null;
  phenotyper_first_name: string | null;
  phenotyper_last_name: string | null;
  latest_upload_on_this_scanner_at: string; // ISO timestamp
  rank_on_scanner: number;
}

/**
 * One scanner's section of the cyl widget — the scanner header plus up to 2
 * cards (one per (experiment, wave) pair).
 */
export interface CylScannerSection {
  scanner_id: number;
  scanner_name: string;
  cards: CylScanRow[];
}

/**
 * Fetch the recent phenotypes grouped per cylinder scanner.
 *
 * Returns one entry per scanner that has at least one row in the view. Empty
 * array if no scanner has any scans visible to the calling role (RLS may
 * hide everything from non-bloom-user callers).
 *
 * Works with both the server-side and client-side Supabase clients —
 * useful because the home page initially server-renders and then
 * client-side Realtime events trigger re-fetches.
 */
export async function getRecentPhenotypesByCylScanner(
  supabase: SupabaseClient<Database>,
): Promise<CylScannerSection[]> {
  // The view name isn't in the generated Database types yet (regenerate via
  // `supabase gen types typescript --local` to drop this cast).
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("recent_experiments_by_cyl_scanner")
    .select("*")
    .order("scanner_name", { ascending: true })
    .order("rank_on_scanner", { ascending: true });

  if (error) {
    throw new Error(
      `Failed to fetch recent phenotypes by cyl scanner: ${error.message}`,
    );
  }

  const rows = (data ?? []) as CylScanRow[];

  // Group consecutive rows by scanner_id. The view orders by scanner_name
  // then rank_on_scanner so all of one scanner's rows arrive together —
  // a single linear pass is enough.
  const sections: CylScannerSection[] = [];
  for (const row of rows) {
    if (!row.scanner_name) continue;

    const last = sections[sections.length - 1];
    if (last && last.scanner_id === row.scanner_id) {
      last.cards.push(row);
    } else {
      sections.push({
        scanner_id: row.scanner_id,
        scanner_name: row.scanner_name,
        cards: [row],
      });
    }
  }

  return sections;
}
