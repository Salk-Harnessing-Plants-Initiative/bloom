/**
 * Top-level component for the home-page "Recent phenotypes by plate
 * scanner" widget — gravi analog of RecentPhenotypesByCylScanner.
 *
 * Server component — fetches the data once on the request, then renders a
 * top-level section heading with one ScannerSection per scanner that has
 * scans on record. Phase 4 wraps the section list in a client component
 * that re-fetches on Realtime INSERT events for gravi_scans / gravi_images.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { getRecentPhenotypesByPlateScanner } from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { ScannerSection } from "./ScannerSection";

export async function RecentPhenotypesByPlateScanner() {
  const supabase = await createServerSupabaseClient();
  const sections = await getRecentPhenotypesByPlateScanner(supabase);

  return (
    <section className="py-8" aria-label="Recent phenotypes by plate scanner">
      <h2 className="text-2xl font-serif italic text-green-800 mb-5">
        Recent phenotypes by plate scanner
      </h2>

      {sections.length === 0 ? (
        <p className="text-sm text-stone-500">
          No phenotypes yet — once a plate scanner uploads its first session,
          it will appear here.
        </p>
      ) : (
        <div className="space-y-6">
          {sections.map((section) => (
            <ScannerSection key={section.scanner_id} section={section} />
          ))}
        </div>
      )}
    </section>
  );
}
