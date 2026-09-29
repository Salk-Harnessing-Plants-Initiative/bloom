/** Row builders for the pipeline-run views' tests. */

import type { RunRow, RunScanRow } from "../realtime-reducer";
import type { ScanMeta } from "../scan-meta";

export const ME = "4965b3af-ccfe-40f5-814b-447e1f726e1b";
export const OTHER = "0b7e2c91-5d3a-4f10-9e8a-6c2d1b4a7f35";

/** `2026-09-28T10:MM:SS+00:00`, `second` seconds after 10:00:00 (may exceed 59). */
export function at(second: number, fraction = ""): string {
  const m = Math.floor(second / 60);
  const s = second % 60;
  return `2026-09-28T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}${fraction}+00:00`;
}

export function runRow(id: number, created_at: string, overrides: Partial<RunRow> = {}): RunRow {
  return {
    id,
    created_at,
    completed_at: null,
    done_count: 0,
    error_message: null,
    failed_count: 0,
    params: {},
    requested_by: ME,
    reused_count: 0,
    scan_count: 40,
    status: "running",
    submitted_at: null,
    target_id: 5,
    target_level: "experiment",
    ...overrides,
  };
}

export function scanRow(id: number, scan_id: number, overrides: Partial<RunScanRow> = {}): RunScanRow {
  return {
    id,
    run_id: 91,
    scan_id,
    status: "queued",
    attempts: 0,
    argo_workflow_name: null,
    batch_index: null,
    created_at: at(1),
    updated_at: at(1),
    error_message: null,
    source_id: null,
    ...overrides,
  };
}

export function scanMeta(scan_id: number, overrides: Partial<ScanMeta> = {}): ScanMeta {
  return {
    scan_id,
    qr_code: `QR-${scan_id}`,
    wave_id: 11,
    wave_number: 1,
    plant_age_days: 14,
    species_id: 2,
    species_name: "pennycress",
    accession_id: 7,
    experiment_id: 5,
    ...overrides,
  };
}
