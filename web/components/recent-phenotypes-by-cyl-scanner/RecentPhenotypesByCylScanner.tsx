/**
 * Top-level component for the home-page "Recent phenotypes by cylinder
 * scanner" widget.
 *
 * Server component — fetches the data once on the request, then renders a
 * top-level section heading with one ScannerSection per scanner that has
 * scans on record. Phase 4 wraps the section list in a client component that
 * re-fetches on Realtime INSERT events for cyl_scans.
 *
 * Empty state: still renders the heading so the page communicates "this is
 * where new scans will appear once a cylinder scanner uploads its first
 * session."
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { getRecentPhenotypesByCylScanner } from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { ScannerSection } from "./ScannerSection";

export async function RecentPhenotypesByCylScanner() {
  const supabase = await createServerSupabaseClient();
  const sections = await getRecentPhenotypesByCylScanner(supabase);

  return (
    <section className="py-8" aria-label="Recent phenotypes by cylinder scanner">
      <h2 className="text-2xl font-serif italic text-green-800 mb-5">
        Recent phenotypes by cylinder scanner
      </h2>

      {sections.length === 0 ? (
        <p className="text-sm text-stone-500">
          No phenotypes yet — once a cylinder scanner uploads its first session,
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
