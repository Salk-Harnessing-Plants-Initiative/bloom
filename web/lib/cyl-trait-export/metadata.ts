/**
 * The 22 metadata columns of a trait export, cell for cell what bloomctl's
 * `cyl download` writes to `scans.csv` (bloomcli/src/bloomctl/cyl/download.py
 * `_COLUMNS`, `build_scan_row`, `scan_relative_dir`), so the two files join on
 * `scan_id`. `__fixtures__/scan-metadata-parity.json` pins the equivalence.
 */

/** A `cyl_scans_extended` row as PostgREST returns it. */
export type ScanExtendedRow = Record<string, string | number | null>

/** (output column, source key in a cyl_scans_extended row); null = derived. */
const COLUMNS: ReadonlyArray<readonly [string, string | null]> = [
  ['scan_id', 'scan_id'],
  ['plant_qr_code', 'qr_code'],
  ['scan_path', null],
  ['scanner_id', 'scanner_id'],
  ['species_id', 'species_id'],
  ['species_name', 'species_name'],
  ['species_genus', 'species_genus'],
  ['species_species', 'species_species'],
  ['uploaded_at', 'uploaded_at'],
  ['wave_id', 'wave_id'],
  ['wave_number', 'wave_number'],
  ['wave_name', 'wave_name'],
  ['accession_id', 'accession_id'],
  ['genotype', null],
  ['date_scanned', 'date_scanned'],
  ['experiment_id', 'experiment_id'],
  ['experiment_name', 'experiment_name'],
  ['germ_day', 'germ_day'],
  ['germ_day_color', 'germ_day_color'],
  ['phenotyper_id', 'phenotyper_id'],
  ['plant_age_days', 'plant_age_days'],
  ['plant_id', 'plant_id'],
]

export const METADATA_COLUMNS: string[] = COLUMNS.map(([name]) => name)

/** Python's `str()` for the JSON scalars PostgREST returns. */
function pyStr(value: unknown): string {
  if (value === null || value === undefined) return 'None'
  return String(value)
}

/** bloomctl's `safe_component` (bloomcli/src/bloomctl/_download.py). */
export function safeComponent(value: unknown): string {
  const cleaned = pyStr(value).replace(/[\\/:\u0000]/g, '_')
  if (cleaned === '' || /^\.+$/.test(cleaned)) return '_'
  return cleaned
}

/** bloomctl's `scan_relative_dir`: the scan's image directory in a `cyl download`. */
export function scanPath(row: ScanExtendedRow): string {
  const wave = row.wave_number || 0 // Python `or 0`: NULL and 0 both give 0
  const day = safeComponent(row.plant_age_days)
  const date = safeComponent(row.date_scanned)
  const qr = safeComponent(row.qr_code)
  return `images/Wave${safeComponent(wave)}/Day${day}_${date}/${qr}`
}

/** A csv.DictWriter cell: NULL (or a missing key) is an empty cell. */
function cell(value: string | number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

/** The 22 metadata cells for one scan, in `METADATA_COLUMNS` order. */
export function metadataCells(row: ScanExtendedRow, genotype: string | null): string[] {
  return COLUMNS.map(([name, key]) => {
    if (name === 'scan_path') return scanPath(row)
    if (name === 'genotype') return genotype ?? ''
    return cell(key === null ? null : row[key])
  })
}
