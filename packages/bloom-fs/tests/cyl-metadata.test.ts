import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'
import * as xlsx from 'xlsx'

import { getImageMetadata } from '../src/cyl/metadata'
import type { CylImageMetadata } from '../src/cyl/metadata'

const CAMERA_FIELDS = [
  'num_frames',
  'exposure_time',
  'gain',
  'brightness',
  'contrast',
  'gamma',
  'seconds_per_rot',
] as const

let root: string

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'bloom-fs-cyl-'))
  vi.spyOn(console, 'log').mockImplementation(() => {})

  fs.writeFileSync(
    path.join(root, 'cyl-metadata.yml'),
    [
      'path_format_string: <experiment>/<plant_qr_code>/<frame_number>.png',
      'fixed_values:',
      '  species: Arabidopsis',
      'accession_info:',
      '  spreadsheet: accessions.xlsx',
      '  sheet: Sheet1',
      '  qr_code_col: QR',
      '  accession_col: Accession',
      '',
    ].join('\n')
  )

  const workbook = xlsx.utils.book_new()
  xlsx.utils.book_append_sheet(
    workbook,
    xlsx.utils.aoa_to_sheet([
      ['QR', 'Accession'],
      ['PLANT-1', 'Col-0'],
    ]),
    'Sheet1'
  )
  xlsx.writeFile(workbook, path.join(root, 'accessions.xlsx'))

  fs.mkdirSync(path.join(root, 'exp-1', 'PLANT-1'), { recursive: true })
  fs.writeFileSync(path.join(root, 'exp-1', 'PLANT-1', '1.png'), '')
})

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true })
  vi.restoreAllMocks()
})

describe('getImageMetadata camera settings', () => {
  it('includes every camera setting, defaulting to 0', async () => {
    const { metadata } = await getImageMetadata(root)
    expect(metadata).toHaveLength(1)
    for (const field of CAMERA_FIELDS) {
      expect(metadata[0][field], field).toBe(0)
    }
  })

  it('still parses the path fields and accession', async () => {
    const { metadata } = await getImageMetadata(root)
    expect(metadata[0]).toMatchObject({
      species: 'Arabidopsis',
      experiment: 'exp-1',
      plant_qr_code: 'PLANT-1',
      frame_number: 1,
      accession_name: 'Col-0',
    })
  })

  it('lets callers set camera settings on the metadata type', () => {
    // Bloom Desktop builds CylImageMetadata itself with real camera values.
    const metadata: CylImageMetadata = {
      species: 'Arabidopsis',
      experiment: 'exp-1',
      wave_number: 0,
      germ_day: undefined,
      germ_day_color: undefined,
      plant_age_days: 14,
      date_scanned: undefined,
      device_name: undefined,
      plant_qr_code: 'PLANT-1',
      frame_number: 1,
      accession_name: 'Col-0',
      phenotyper_name: undefined,
      phenotyper_email: undefined,
      scientist_name: undefined,
      scientist_email: undefined,
      num_frames: 72,
      exposure_time: 10000,
      gain: 100,
      gamma: 1.2,
      seconds_per_rot: 7.5,
    }
    expect(metadata.gamma).toBe(1.2)
  })
})
