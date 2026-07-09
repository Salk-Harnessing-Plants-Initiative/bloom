import type { SupabaseClient } from "@supabase/supabase-js";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { ScanRow } from "./plateGrouping";

export interface ExperimentRow {
  id: number;
  name: string;
  system_name: string | null;
  species: { id: number; common_name: string | null } | null;
  cyl_scientists: { scientist_name: string | null; email: string | null } | null;
  accessions: { name: string | null } | null;
  gravi_scans: ScanRow[];
}

export async function getExperiment(
  experimentId: number,
): Promise<ExperimentRow | null> {
  const supabase = await createServerSupabaseClient();

  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_experiments")
    .select(
      "id, name, system_name, species(id, common_name), cyl_scientists(scientist_name, email), accessions(name), gravi_scans(id, plate_id, plate_index, wave_number, capture_date, gravi_images(object_path), gravi_scan_metadata_accession(plate_id, accession_name, wave_number, gravi_scan_metadata_sections(plate_section_id, medium, gravi_scan_metadata_section_plants(plant_qr))))",
    )
    .eq("id", experimentId)
    .single();

  if (error) {
    console.error(
      "[plate-phenotypes/[speciesId]/[experimentId]] supabase error:",
      error,
    );
  }

  return (data as ExperimentRow | null) ?? null;
}
