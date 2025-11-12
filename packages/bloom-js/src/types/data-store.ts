import { Database, Json } from './database.types.js'
import { PostgrestError } from '@supabase/supabase-js'

export type Species = Database['public']['Tables']['species']['Insert']
export type SpeciesUpdate = Database['public']['Tables']['species']['Update']
export type SpeciesRow = Database['public']['Tables']['species']['Row']
export type CylScannersRow = Database['public']['Tables']['cyl_scanners']['Row']

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
}
