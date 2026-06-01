/**
 * Query helper for the "Recent phenotypes by plate scanner" home-page widget.
 * Reads from the `recent_phenotypes_by_plate_scanner` Postgres view
 * (defined in `supabase/migrations/20260528120300_*.sql`).
 *
 * Gravi analog of the cyl helper. Gravi has no separate waves or plants
 * tables, so the "(experiment, wave)" pair on cyl maps here to
 * (experiment_id, wave_number) — wave_number lives directly on gravi_scans.
 *
 * The view already does the major joins:
 *   - Joins gravi_scans → gravi_images, gravi_scan_sessions,
 *     gravi_experiments, species, phenotypers
 *   - Picks the most-recent-upload row per (scanner, experiment, wave_number)
 *     using MAX(gravi_images.uploaded_at)
 *   - Ranks pairs within each scanner and filters to the top 2
 *   - Respects RLS (security_invoker = true)
 *
 * This helper just fetches all view rows and groups them per scanner.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

/**
 * One row from `recent_phenotypes_by_plate_scanner` — a (scanner, experiment,
 * wave_number) tuple ranked in the top 2 for its scanner.
 */
export interface PlateScanRow {
  scanner_id: number;
  scanner_name: string | null;
  experiment_id: number;
  experiment_name: string;
  species_id: number | null;
  species_common_name: string | null;
  wave_number: number | null;
  scan_mode: string | null; // 'single' | 'continuous' (per chk_scan_mode)
  plate_id: string | null;
  phenotyper_first_name: string | null;
  phenotyper_last_name: string | null;
  latest_upload_on_this_scanner_at: string; // ISO timestamp
  rank_on_scanner: number;
}

/**
 * One scanner's section of the plate widget — the scanner header plus up to 2
 * cards (one per (experiment, wave_number) pair).
 */
export interface PlateScannerSection {
  scanner_id: number;
  scanner_name: string;
  cards: PlateScanRow[];
}

/**
 * Fetch the recent phenotypes grouped per plate scanner.
 *
 * Returns one entry per scanner that has at least one row in the view. Empty
 * array if no scanner has any scans visible to the calling role (RLS may
 * hide everything from non-bloom-user callers).
 */
export async function getRecentPhenotypesByPlateScanner(
  supabase: SupabaseClient<Database>,
): Promise<PlateScannerSection[]> {
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("recent_phenotypes_by_plate_scanner")
    .select("*")
    .order("scanner_name", { ascending: true })
    .order("rank_on_scanner", { ascending: true });

  if (error) {
    throw new Error(
      `Failed to fetch recent phenotypes by plate scanner: ${error.message}`,
    );
  }

  const rows = (data ?? []) as PlateScanRow[];

  // Group consecutive rows by scanner_id — view ordering guarantees same-
  // scanner rows arrive together so one linear pass is enough.
  const sections: PlateScannerSection[] = [];
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
