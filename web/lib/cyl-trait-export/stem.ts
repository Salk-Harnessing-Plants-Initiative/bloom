/**
 * The export stem (design D6): `<slug>[_wave<W>][_day<A>][_scan<id>]_<keyseg>_<yyyymmdd>`.
 * The zip is `<stem>.zip` and holds `<stem>.csv`, `<stem>.export.json` and
 * `<stem>.excluded.csv`. The result is checked to be header-safe before it is used in
 * `Content-Disposition`.
 */

import type { Selection } from './selection'

export type StemInput = {
  experimentName: string
  experimentId: number
  selection: Selection
  recipeKey: string
  generatedAt: Date
}

const HEX_KEY = /^[0-9a-f]{64}$/
const LEGACY_KEY = /^legacy:([0-9]+)$/
const SAFE_STEM = /^[a-z0-9_-]+$/

/** Runs outside `[A-Za-z0-9-]` become `-`, lowercased, cut to 60, trimmed of `-`. */
export function slugify(name: string, experimentId: number): string {
  const slug = name
    .replace(/[^A-Za-z0-9-]+/g, '-')
    .toLowerCase()
    .slice(0, 60)
    .replace(/^-+|-+$/g, '')
  return slug || `experiment-${experimentId}`
}

/** First 8 of a 64-hex key, `legacy-N` in full, or `unattributed`. */
export function keySegment(key: string): string {
  if (HEX_KEY.test(key)) return key.slice(0, 8)
  const legacy = LEGACY_KEY.exec(key)
  if (legacy) return `legacy-${legacy[1]}`
  if (key === 'unattributed') return key
  throw new Error(`not a recipe key: ${key}`)
}

export function buildStem(input: StemInput): string {
  const sel = input.selection
  const parts = [slugify(input.experimentName, input.experimentId)]
  if ('scan' in sel) {
    parts.push(`scan${sel.scan}`)
  } else {
    if (sel.wave !== undefined) parts.push(`wave${sel.wave}`)
    if (sel.age !== undefined) parts.push(`day${sel.age}`)
  }
  parts.push(keySegment(input.recipeKey))
  parts.push(input.generatedAt.toISOString().slice(0, 10).replace(/-/g, ''))
  const stem = parts.join('_')
  if (!SAFE_STEM.test(stem)) throw new Error(`unsafe export stem: ${stem}`)
  return stem
}
