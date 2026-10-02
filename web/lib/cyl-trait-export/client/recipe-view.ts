/**
 * What the Download traits dialog shows for a listing (design D8; spec "Trait download
 * dialog recipe list"). Client-safe: value imports only from `stem.ts`.
 */

import type { RecipeRow } from '../recipes'
import { keySegment } from '../stem'

/** A filter's value: a wave or age (0 is a value), or every one. */
export type FilterValue = number | 'all'

export const NO_SCANS = 'No scans match this wave and age'
export const NO_RECIPES = 'No trait results for this selection'

const SHORT_SHA = 7

/** The recipe's `<keyseg>`, or its raw key if it is not one we know. */
export function recipeLabel(key: string): string {
  try {
    return keySegment(key)
  } catch {
    return key
  }
}

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

/** Lines saying what the recipe is, from its `definition`; a missing field is left out. */
export function describeRecipe(row: Pick<RecipeRow, 'recipe_kind' | 'definition'>): string[] {
  const def = row.definition ?? {}
  if (row.recipe_kind === 'unattributed') return ['no source recorded']
  if (row.recipe_kind === 'legacy') {
    const name = str(def.source_name)
    return name ? [`Source: ${name}`] : []
  }
  const lines: string[] = []
  const models = Array.isArray(def.models)
    ? def.models.flatMap((m) => {
        if (!Array.isArray(m)) return []
        const name = str(m[0])
        if (!name) return []
        const version = str(m[1])
        const sum = shortChecksum(str(m[2]))
        return [[name, version, sum && `(${sum})`].filter(Boolean).join(' ')]
      })
    : []
  if (models.length > 0) lines.push(`Models: ${models.join(', ')}`)
  const code = [
    ['predict', str(def.predict_code_sha)],
    ['traits', str(def.traits_code_sha)],
  ].flatMap(([what, sha]) => (sha ? [`${what} ${sha.slice(0, SHORT_SHA)}`] : []))
  if (code.length > 0) lines.push(`Code: ${code.join(', ')}`)
  const params = def.predict_output_params
  if (typeof params === 'object' && params !== null && !Array.isArray(params)) {
    const names = Object.keys(params).sort()
    if (names.length > 0) lines.push(`Output params: ${names.join(', ')}`)
  }
  return lines.length > 0 ? lines : ['no models or code recorded']
}

/** `sha256:<hex>` as `sha256:<first 7>`; a bare value is cut to 7. */
function shortChecksum(sum: string | null): string | null {
  if (sum === null) return null
  const at = sum.indexOf(':')
  return at < 0
    ? sum.slice(0, SHORT_SHA)
    : `${sum.slice(0, at)}:${sum.slice(at + 1, at + 1 + SHORT_SHA)}`
}

/**
 * The recipe to point at when the default covers fewer scans, or null. Never
 * `unattributed`, whose data has no recorded provenance; the first in listing order
 * on a tie.
 */
export function fewerScansNote(
  rows: RecipeRow[]
): { key: string; label: string; nScans: number } | null {
  const def = rows.find((r) => r.is_default)
  if (def === undefined) return null
  let best: RecipeRow | null = null
  for (const r of rows) {
    if (r === def || r.recipe_kind === 'unattributed') continue
    if (best === null || r.n_scans > best.n_scans) best = r
  }
  if (best === null || best.n_scans <= def.n_scans) return null
  return { key: best.recipe_key, label: recipeLabel(best.recipe_key), nScans: best.n_scans }
}

/** The page's value when the loaded list has it, otherwise "All". */
export function prefill(value: number | undefined, loaded: number[]): FilterValue {
  return value !== undefined && loaded.includes(value) ? value : 'all'
}

/** Why there is nothing to download, or null. */
export function emptyState(nSelected: number, rows: RecipeRow[]): string | null {
  if (nSelected === 0) return NO_SCANS
  if (rows.length === 0) return NO_RECIPES
  return null
}

const fmt = (n: number) => n.toLocaleString('en-US')

/** "N of M selected scans". */
export function countLabel(n: number, m: number): string {
  return `${fmt(n)} of ${fmt(m)} selected scans`
}

/** The dialog heading's subject: the experiment and its filters, or the scan. */
export function selectionTitle(
  target: { experimentId: number } | { scanId: number },
  wave: FilterValue,
  age: FilterValue
): string {
  if ('scanId' in target) return `scan ${target.scanId}`
  const parts = [`experiment ${target.experimentId}`]
  if (wave !== 'all') parts.push(`wave ${wave}`)
  if (age !== 'all') parts.push(`day ${age}`)
  return parts.join(' · ')
}
