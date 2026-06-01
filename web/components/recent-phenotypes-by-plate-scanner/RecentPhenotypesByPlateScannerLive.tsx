"use client";

/**
 * Client-side wrapper for the "Recent phenotypes by plate scanner" widget.
 * Subscribes to Supabase Realtime INSERT events on BOTH gravi_scans (new
 * scan records) and gravi_images (new uploads against existing scans),
 * because both signals can change which (experiment, wave_number) pair
 * floats to the top of a scanner's list.
 */

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  getRecentPhenotypesByPlateScanner,
  type PlateScannerSection,
} from "@/lib/queries/recent-phenotypes-by-plate-scanner";
import { ScannerSection } from "./ScannerSection";

interface Props {
  initialSections: PlateScannerSection[];
}

export function RecentPhenotypesByPlateScannerLive({ initialSections }: Props) {
  const [sections, setSections] = useState<PlateScannerSection[]>(
    initialSections,
  );

  useEffect(() => {
    const supabase = createClientSupabaseClient();

    const refresh = async () => {
      try {
        const fresh = await getRecentPhenotypesByPlateScanner(supabase);
        setSections(fresh);
      } catch {
        // Re-fetch failures are non-fatal — initial server-rendered list
        // stays visible. Next Realtime event will retry.
      }
    };

    const channel = supabase
      .channel("recent-plate-phenotypes")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "gravi_scans" },
        refresh,
      )
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "gravi_images" },
        refresh,
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  if (sections.length === 0) {
    return (
      <p className="text-sm text-stone-500">
        No phenotypes yet — once a plate scanner uploads its first session,
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
