import { createServerSupabaseClient } from "@/lib/supabase/server";
import ScanBatches, { type ScanBatch } from "./ScanBatches";

/** Cylinder scan batches, from cyl_wave_timeline. */
export default async function CylinderPanel() {
  const supabase = await createServerSupabaseClient();
  const { data } = await supabase.from("cyl_wave_timeline").select("*");
  return (
    <div>
      <h2 className="mb-6 text-xl font-serif italic">Cylinder scanner usage tracking</h2>
      <ScanBatches
        rows={(data ?? []) as ScanBatch[]}
        batchesLabel="Batches of cylinders scanned."
        calendarLabel="Total cylinders scanned on each day."
        empty="No cylinder scans yet."
      />
    </div>
  );
}
