/**
 * Top-level server component for the home-page "Recent phenotypes by
 * cylinder scanner" widget.
 *
 * Fetches the initial section list server-side for fast first paint, then
 * hands off to the client wrapper which subscribes to Realtime INSERT
 * events on cyl_scans and re-fetches the view when new scans land.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { getRecentPhenotypesByCylScanner } from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { RecentPhenotypesByCylScannerLive } from "./RecentPhenotypesByCylScannerLive";
import { LiveIndicator } from "./LiveIndicator";

export async function RecentPhenotypesByCylScanner() {
  const supabase = await createServerSupabaseClient();
  const initialSections = await getRecentPhenotypesByCylScanner(supabase);

  return (
    <section className="py-8" aria-label="Recent scans by cylinder scanner">
      <div className="mb-5 flex items-center gap-3">
        <h2 className="text-2xl font-serif italic text-green-800">
          Recent Scans by cylinder scanner
        </h2>
        <LiveIndicator />
      </div>
      <RecentPhenotypesByCylScannerLive initialSections={initialSections} />
    </section>
  );
}
