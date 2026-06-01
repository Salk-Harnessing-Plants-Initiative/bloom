"use client";

/**
 * Client-side wrapper for the "Recent phenotypes by cylinder scanner"
 * widget. Renders the server-supplied initial sections, then subscribes to
 * Supabase Realtime INSERT events on `cyl_scans` and re-fetches the view
 * (recent_experiments_by_cyl_scanner) whenever a new scan lands.
 *
 * The list renders here instead of in the parent server component so the
 * useEffect / state can swap the cards in place without a full route
 * navigation. The heading + section wrapper stays in the server component
 * for fast first paint.
 */

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  getRecentPhenotypesByCylScanner,
  type CylScannerSection,
} from "@/lib/queries/recent-phenotypes-by-cyl-scanner";
import { ScannerSection } from "./ScannerSection";

interface Props {
  initialSections: CylScannerSection[];
}

export function RecentPhenotypesByCylScannerLive({ initialSections }: Props) {
  const [sections, setSections] = useState<CylScannerSection[]>(initialSections);

  useEffect(() => {
    const supabase = createClientSupabaseClient();

    const channel = supabase
      .channel("recent-cyl-phenotypes")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "cyl_scans" },
        async () => {
          try {
            const fresh = await getRecentPhenotypesByCylScanner(supabase);
            setSections(fresh);
          } catch {
            // Re-fetch failures are non-fatal — the next INSERT will retry
            // and the initial server-rendered list stays visible. We don't
            // surface to the user because the widget is informational, not
            // a primary workflow.
          }
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  if (sections.length === 0) {
    return (
      <p className="text-sm text-stone-500">
        No phenotypes yet — once a cylinder scanner uploads its first session,
        it will appear here.
      </p>
    );
  }

  return (
    <div className="divide-y divide-stone-200">
      {sections.map((section) => (
        <div key={section.scanner_id} className="py-3 first:pt-0 last:pb-0">
          <ScannerSection section={section} />
        </div>
      ))}
    </div>
  );
}
