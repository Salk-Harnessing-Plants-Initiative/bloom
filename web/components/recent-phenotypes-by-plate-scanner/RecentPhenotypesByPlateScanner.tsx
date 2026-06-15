/**
 * Top-level server component for the home-page "Recent phenotypes by plate
 * scanner" widget — gravi analog of RecentPhenotypesByCylScanner.
 *
 * Fetches the initial section list server-side, then hands off to the
 * client wrapper which subscribes to Realtime INSERT events on
 * gravi_scans + gravi_images and re-fetches when new uploads land.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { getRecentPhenotypesByPlateScanner } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { RecentPhenotypesByPlateScannerLive } from "./RecentPhenotypesByPlateScannerLive";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";

export async function RecentPhenotypesByPlateScanner() {
  const supabase = await createServerSupabaseClient();
  const initialSections = await getRecentPhenotypesByPlateScanner(supabase);

  return (
    <section className="py-8" aria-label="Recent scans by plate scanner">
      <div className="mb-5 flex items-center gap-3">
        <h2 className="text-2xl font-serif italic text-green-800">
          Recent Scans by plate scanner
        </h2>
        <LiveIndicator />
      </div>
      <RecentPhenotypesByPlateScannerLive initialSections={initialSections} />
    </section>
  );
}
