/**
 * The `cyl_scans_extended` columns the pipeline-run views read about a
 * requested scan. Not `experiment_name`: the view runs with its owner's
 * rights, so its names include soft-deleted experiments. Names come from the
 * security-invoker `cyl_pipeline_run_experiments` instead.
 */

export const SCAN_META_COLUMNS =
  "scan_id, qr_code, wave_id, wave_number, plant_age_days, species_id, species_name, accession_id, experiment_id";

export interface ScanMeta {
  scan_id: number;
  qr_code: string | null;
  wave_id: number | null;
  wave_number: number | null;
  plant_age_days: number | null;
  species_id: number | null;
  species_name: string | null;
  accession_id: number | null;
  experiment_id: number | null;
}
