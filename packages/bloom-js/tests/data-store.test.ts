import { describe, it, expect, vi } from 'vitest'

import { SupabaseStore } from '../src/core/supabase/data-store'
import type {
  CylImageMetadata,
  GraviImageMetadata,
  GraviScanMetadataParams,
  GraviScanSessionParams,
} from '../src'

// A minimal fake of the Supabase client: records rpc/table calls and returns
// whatever the test sets as the result.
function fakeSupabase(result: { data?: unknown; error?: unknown } = {}) {
  const response = { data: result.data ?? null, error: result.error ?? null }
  const table = {
    upsert: vi.fn().mockResolvedValue(response),
    select: vi.fn().mockResolvedValue(response),
  }
  const client = {
    rpc: vi.fn().mockResolvedValue(response),
    from: vi.fn().mockReturnValue(table),
  }
  return { client, table }
}

function storeWith(result?: { data?: unknown; error?: unknown }) {
  const fake = fakeSupabase(result)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { store: new SupabaseStore(fake.client as any), ...fake }
}

const POSTGREST_ERROR = { message: 'boom', details: '', hint: '', code: 'XX000' }

const cylImage: CylImageMetadata = {
  species: 'Arabidopsis',
  experiment: 'exp-1',
  wave_number: 0,
  germ_day: 3,
  germ_day_color: 'red',
  plant_age_days: 14,
  date_scanned: '2026-10-16',
  device_name: 'PBIOBScanner',
  plant_qr_code: 'PLANT-1',
  accession_name: 'Col-0',
  frame_number: 1,
  phenotyper_name: 'Ben',
  phenotyper_email: 'ben@salk.edu',
  scientist_name: 'Sci',
  scientist_email: 'sci@salk.edu',
}

const graviImage: GraviImageMetadata = {
  species: 'Arabidopsis',
  experiment: 'gravi-1',
  scanner_name: 'V600-1',
  phenotyper_name: 'Ben',
  phenotyper_email: 'ben@salk.edu',
  scientist_name: 'Sci',
  scientist_email: 'sci@salk.edu',
  plate_barcode: 'PLATE-1',
  capture_date: '2026-10-16T10:00:00Z',
  grid_mode: '2grid',
  plate_index: '00',
  resolution: 1200,
  format: 'tiff',
}

describe('insertImageMetadata (cylinder images)', () => {
  it('calls insert_image_v3_0 with the camera settings', async () => {
    const { store, client } = storeWith({ data: 7 })
    const result = await store.insertImageMetadata({
      ...cylImage,
      num_frames: 72,
      exposure_time: 10000,
      gain: 100,
      brightness: 3,
      contrast: 4,
      gamma: 1.2,
      seconds_per_rot: 7,
    })
    expect(result).toEqual({ created: 7, error: null })
    expect(client.rpc).toHaveBeenCalledWith('insert_image_v3_0', {
      species_common_name: 'Arabidopsis',
      experiment: 'exp-1',
      wave_number: 0,
      germ_day: 3,
      germ_day_color: 'red',
      plant_age_days: 14,
      date_scanned_: '2026-10-16',
      device_name: 'PBIOBScanner',
      plant_qr_code: 'PLANT-1',
      accession_name: 'Col-0',
      frame_number_: 1,
      phenotyper_name: 'Ben',
      phenotyper_email: 'ben@salk.edu',
      scientist_name: 'Sci',
      scientist_email: 'sci@salk.edu',
      num_frames: 72,
      exposure_time: 10000,
      gain: 100,
      brightness: 3,
      contrast: 4,
      gamma: 1.2,
      seconds_per_rot: 7,
    })
  })

  it('sends 0 for camera settings that are missing', async () => {
    const { store, client } = storeWith()
    await store.insertImageMetadata(cylImage)
    const args = client.rpc.mock.calls[0][1]
    for (const field of [
      'num_frames',
      'exposure_time',
      'gain',
      'brightness',
      'contrast',
      'gamma',
      'seconds_per_rot',
    ]) {
      expect(args[field], field).toBe(0)
    }
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.insertImageMetadata(cylImage)).toEqual({
      created: null,
      error: POSTGREST_ERROR,
    })
  })
})

describe('insertGraviImageMetadata', () => {
  it('calls insert_gravi_image with every field mapped', async () => {
    const { store, client } = storeWith({ data: 42 })
    const result = await store.insertGraviImageMetadata({
      ...graviImage,
      accession_name: 'Col-0',
      cycle_number: 3,
      session_id: 9,
      system_name: 'rig-1',
      metadata_id: 5,
      wave_number: 0,
      transplant_date: '2026-10-01',
      custom_note: 'note',
    })
    expect(result).toEqual({ created: 42, error: null })
    expect(client.rpc).toHaveBeenCalledWith('insert_gravi_image', {
      species_common_name: 'Arabidopsis',
      experiment_name: 'gravi-1',
      scanner_name: 'V600-1',
      phenotyper_name: 'Ben',
      phenotyper_email: 'ben@salk.edu',
      scientist_name: 'Sci',
      scientist_email: 'sci@salk.edu',
      plate_barcode_: 'PLATE-1',
      capture_date_: '2026-10-16T10:00:00Z',
      grid_mode_: '2grid',
      plate_index_: '00',
      resolution_: 1200,
      format_: 'tiff',
      accession_name: 'Col-0',
      cycle_number_: 3,
      session_id_: 9,
      system_name_: 'rig-1',
      metadata_id_: 5,
      wave_number_: 0,
      transplant_date_: '2026-10-01',
      custom_note_: 'note',
    })
  })

  it('sends an empty plate barcode when there is none', async () => {
    const { store, client } = storeWith()
    await store.insertGraviImageMetadata({ ...graviImage, plate_barcode: null })
    expect(client.rpc.mock.calls[0][1].plate_barcode_).toBe('')
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.insertGraviImageMetadata(graviImage)).toEqual({
      created: null,
      error: POSTGREST_ERROR,
    })
  })
})

describe('updateGraviImageMetadata', () => {
  it('upserts gravi_images on scan_id, ignoring duplicates, with hash and size', async () => {
    const { store, client, table } = storeWith()
    const result = await store.updateGraviImageMetadata(11, {
      object_path: 'graviscan/11.tif',
      file_hash: 'abc',
      file_size_bytes: 1234,
    })
    expect(result).toEqual({ error: null })
    expect(client.from).toHaveBeenCalledWith('gravi_images')
    expect(table.upsert).toHaveBeenCalledWith(
      {
        scan_id: 11,
        object_path: 'graviscan/11.tif',
        file_hash: 'abc',
        file_size_bytes: 1234,
      },
      { onConflict: 'scan_id', ignoreDuplicates: true }
    )
  })

  it('accepts just the object path', async () => {
    const { store, table } = storeWith()
    await store.updateGraviImageMetadata(11, { object_path: 'p' })
    expect(table.upsert.mock.calls[0][0]).toEqual({
      scan_id: 11,
      object_path: 'p',
      file_hash: undefined,
      file_size_bytes: undefined,
    })
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.updateGraviImageMetadata(11, { object_path: 'p' })).toEqual({
      error: POSTGREST_ERROR,
    })
  })
})

describe('getAllGraviScanners', () => {
  it('selects every row from gravi_scanners', async () => {
    const rows = [{ id: 1, name: 'V600-1' }]
    const { store, client, table } = storeWith({ data: rows })
    expect(await store.getAllGraviScanners()).toEqual({ data: rows, error: null })
    expect(client.from).toHaveBeenCalledWith('gravi_scanners')
    expect(table.select).toHaveBeenCalledWith('*')
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.getAllGraviScanners()).toEqual({ data: null, error: POSTGREST_ERROR })
  })
})

describe('insertGraviScanSession', () => {
  const session: GraviScanSessionParams = {
    species: 'Arabidopsis',
    experiment: 'gravi-1',
    phenotyper_name: 'Ben',
    phenotyper_email: 'ben@salk.edu',
    scientist_name: 'Sci',
    scientist_email: 'sci@salk.edu',
  }

  it('calls insert_gravi_scan_session, defaulting scan mode to single and cancelled to false', async () => {
    const { store, client } = storeWith({ data: 3 })
    expect(await store.insertGraviScanSession(session)).toEqual({ created: 3, error: null })
    expect(client.rpc).toHaveBeenCalledWith(
      'insert_gravi_scan_session',
      expect.objectContaining({
        species_common_name: 'Arabidopsis',
        experiment_name: 'gravi-1',
        scan_mode_: 'single',
        cancelled_: false,
      })
    )
  })

  it('passes the optional session fields through', async () => {
    const { store, client } = storeWith()
    await store.insertGraviScanSession({
      ...session,
      accession_name: 'Col-0',
      scan_mode: 'continuous',
      interval_seconds: 300,
      duration_seconds: 3600,
      total_cycles: 12,
      actual_duration_seconds: 3590,
      completed_at: '2026-10-16T11:00:00Z',
      cancelled: true,
      system_name: 'rig-1',
    })
    expect(client.rpc.mock.calls[0][1]).toEqual({
      species_common_name: 'Arabidopsis',
      experiment_name: 'gravi-1',
      phenotyper_name: 'Ben',
      phenotyper_email: 'ben@salk.edu',
      scientist_name: 'Sci',
      scientist_email: 'sci@salk.edu',
      accession_name: 'Col-0',
      scan_mode_: 'continuous',
      interval_seconds_: 300,
      duration_seconds_: 3600,
      total_cycles_: 12,
      actual_duration_seconds_: 3590,
      completed_at_: '2026-10-16T11:00:00Z',
      cancelled_: true,
      system_name_: 'rig-1',
    })
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.insertGraviScanSession(session)).toEqual({
      created: null,
      error: POSTGREST_ERROR,
    })
  })
})

describe('insertGraviScanMetadata', () => {
  const metadata: GraviScanMetadataParams = {
    accession_name: 'Col-0',
    plate_id: 'PLATE-1',
    wave_number: 0,
    sections: [{ plate_section_id: 'A', plant_qr: 'QR-1', medium: 'MS' }],
  }

  it('calls insert_gravi_scan_metadata with the sections as JSON', async () => {
    const { store, client } = storeWith({ data: 8 })
    expect(await store.insertGraviScanMetadata(metadata)).toEqual({ created: 8, error: null })
    expect(client.rpc).toHaveBeenCalledWith('insert_gravi_scan_metadata', {
      accession_name_: 'Col-0',
      plate_id_: 'PLATE-1',
      sections_: [{ plate_section_id: 'A', plant_qr: 'QR-1', medium: 'MS' }],
      wave_number_: 0,
      transplant_date_: undefined,
      custom_note_: undefined,
    })
  })

  it('turns a null transplant date or note into undefined', async () => {
    const { store, client } = storeWith()
    await store.insertGraviScanMetadata({
      ...metadata,
      transplant_date: null,
      custom_note: null,
    })
    const args = client.rpc.mock.calls[0][1]
    expect(args.transplant_date_).toBeUndefined()
    expect(args.custom_note_).toBeUndefined()
  })

  it('returns the database error', async () => {
    const { store } = storeWith({ error: POSTGREST_ERROR })
    expect(await store.insertGraviScanMetadata(metadata)).toEqual({
      created: null,
      error: POSTGREST_ERROR,
    })
  })
})
