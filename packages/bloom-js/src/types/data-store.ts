import { Database, Json } from './database.types.js'
import { PostgrestError } from '@supabase/supabase-js'

export type Species = Database['public']['Tables']['species']['Insert']
export type SpeciesUpdate = Database['public']['Tables']['species']['Update']
export type SpeciesRow = Database['public']['Tables']['species']['Row']
export type CylScannersRow = Database['public']['Tables']['cyl_scanners']['Row']
export type GraviScannersRow = Database['public']['Tables']['gravi_scanners']['Row']

export type CylImageMetadata = {
  species: string | undefined
  experiment: string | undefined
  wave_number: number | undefined
  germ_day: number | undefined
  germ_day_color: string | undefined
  plant_age_days: number | undefined
  date_scanned: string | undefined
  device_name: string | undefined
  plant_qr_code: string | undefined
  accession_name: string | undefined
  frame_number: number | undefined
  phenotyper_name: string | undefined
  phenotyper_email: string | undefined
  scientist_name: string | undefined
  scientist_email: string | undefined
  // Camera settings, recorded with each image (0 when not given).
  num_frames?: number
  exposure_time?: number
  gain?: number
  brightness?: number
  contrast?: number
  gamma?: number
  seconds_per_rot?: number
}

export type GraviImageMetadata = {
  species: string
  experiment: string
  scanner_name: string
  phenotyper_name: string
  phenotyper_email: string
  scientist_name: string
  scientist_email: string
  plate_barcode: string | null
  capture_date: string
  grid_mode: string
  plate_index: string
  resolution: number
  format: string
  accession_name?: string
  cycle_number?: number
  session_id?: number
  system_name?: string
  metadata_id?: number
  wave_number?: number
  transplant_date?: string
  custom_note?: string
}

export type GraviScanMetadataParams = {
  accession_name: string
  plate_id: string
  wave_number?: number
  transplant_date?: string | null
  custom_note?: string | null
  sections: Array<{
    plate_section_id: string
    plant_qr: string
    medium?: string | null
  }>
}

export type GraviScanSessionParams = {
  species: string
  experiment: string
  phenotyper_name: string
  phenotyper_email: string
  scientist_name: string
  scientist_email: string
  accession_name?: string
  scan_mode?: string
  interval_seconds?: number
  duration_seconds?: number
  total_cycles?: number
  actual_duration_seconds?: number
  completed_at?: string
  cancelled?: boolean
  system_name?: string
}

export interface DataStore {
  addSpecies(species: Species): Promise<{ error: PostgrestError | null }>
  updateSpecies(
    common_name: string,
    fields: SpeciesUpdate
  ): Promise<{
    error: PostgrestError | null
  }>
  getAllSpecies(): Promise<{
    data: SpeciesRow[] | null
    error: PostgrestError | null
  }>
  getExperiments(species_id: number): Promise<{
    data: { name: string }[] | null
    error: PostgrestError | null
  }>
  insertImageMetadata(
    metadata: CylImageMetadata
  ): Promise<{ created: number | null; error: PostgrestError | null }>
  updateImageMetadata(
    image_id: number,
    fields: {
      object_path?: string
      status?: string
    }
  ): Promise<{ error: PostgrestError | null }>
  getAllCylScanners(): Promise<{
    data: CylScannersRow[] | null
    error: PostgrestError | null
  }>
  addCylScanner(name: string): Promise<{ error: PostgrestError | null }>
  deleteCylScanner(name: string): Promise<{ error: PostgrestError | null }>
  insertScRna(
    filename: string,
    species_id: number,
    scientist_id: number,
    url: string,
    assembly: string,
    annotation: string,
    strain: string,
    metadata: Json
  ): Promise<{ error: PostgrestError | string | null; created: number | 0 }>
  insertScRNAgene(
    dataset_id: number,
    scrna_gene_obj: Record<string, any>,
    store: DataStore
  ): Promise<{ created: boolean; error: PostgrestError | null }>
  insertScRNAcells(
    dataset_id: number,
    scRNA_cells: Record<string, any>,
    store: DataStore
  ): Promise<{ created: boolean; error: PostgrestError | null }>
  insertScRNAcounts(
    dataset_id: number,
    scRNAcounts: Record<string, any>,
    store: DataStore,
    filename: string
  ): Promise<{ created: boolean; error: PostgrestError | null }>
  insertGraviImageMetadata(
    metadata: GraviImageMetadata
  ): Promise<{ created: number | null; error: PostgrestError | null }>
  updateGraviImageMetadata(
    scan_id: number,
    fields: { object_path: string; file_hash?: string; file_size_bytes?: number }
  ): Promise<{ error: PostgrestError | null }>
  getAllGraviScanners(): Promise<{
    data: GraviScannersRow[] | null
    error: PostgrestError | null
  }>
  insertGraviScanSession(
    params: GraviScanSessionParams
  ): Promise<{ created: number | null; error: PostgrestError | null }>
  insertGraviScanMetadata(
    params: GraviScanMetadataParams
  ): Promise<{ created: number | null; error: PostgrestError | null }>
}
