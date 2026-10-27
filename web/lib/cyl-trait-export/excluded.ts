/**
 * `<stem>.excluded.csv`: a flat copy of the sidecar's `excluded` list (design D5), in
 * the same CSV format as the traits file. Header-only when nothing is excluded.
 */

import { csvLine } from './csv'
import type { CoverageRow } from './sidecar'

export const EXCLUDED_HEADER = ['scan_id', 'plant_qr_code', 'reason', 'available_recipes']

export function excludedCsv(coverage: CoverageRow[]): string {
  const rows = [...coverage]
    .filter((c) => c.status !== 'included')
    .sort((a, b) => a.scan_id - b.scan_id)
    .map((c) => [String(c.scan_id), c.plant_qr_code, c.status, c.available_recipes.join(';')])
  return [EXCLUDED_HEADER, ...rows].map(csvLine).join('')
}
