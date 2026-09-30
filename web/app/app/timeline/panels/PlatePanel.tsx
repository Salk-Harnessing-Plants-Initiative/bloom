import { createServerSupabaseClient } from "@/lib/supabase/server";
import ScanBatches, { type ScanBatch } from "./ScanBatches";

/** Plate scan batches, from gravi_scan_timeline. */
export default async function PlatePanel() {
  const supabase = await createServerSupabaseClient();
  const { data, error } = await supabase.from("gravi_scan_timeline").select("*");
  return (
    <div>
      <h2 className="mb-6 text-xl font-serif italic">Plate scanner usage tracking</h2>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          Couldn&apos;t load plate scans.
        </p>
      ) : (
        <ScanBatches
          rows={(data ?? []) as ScanBatch[]}
          batchesLabel="Batches of plates scanned."
          calendarLabel="Total plates scanned on each day."
          empty="No plate scans yet."
        />
      )}
    </div>
  );
}
