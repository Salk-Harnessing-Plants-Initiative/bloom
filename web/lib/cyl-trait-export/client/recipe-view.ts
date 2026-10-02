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
        const version = str(m[1])
        return name ? [version ? `${name} ${version}` : name] : []
      })
    : []
  if (models.length > 0) lines.push(`Models: ${models.join(', ')}`)
  const code = [
    ['predict', str(def.predict_code_sha)],
    ['traits', str(def.traits_code_sha)],
  ].flatMap(([what, sha]) => (sha ? [`${what} ${sha.slice(0, SHORT_SHA)}`] : []))
  if (code.length > 0) lines.push(`Code: ${code.join(', ')}`)
  return lines
}

/**
 * The recipe to point at when the default covers fewer scans, or null. Never
 * `unattributed`, whose data has no recorded provenance; the first in listing order
 * on a tie.
 */
export function fewerScansNote(rows: RecipeRow[]): { label: string; nScans: number } | null {
  const def = rows.find((r) => r.is_default)
  if (def === undefined) return null
  let best: RecipeRow | null = null
  for (const r of rows) {
    if (r === def || r.recipe_kind === 'unattributed') continue
    if (best === null || r.n_scans > best.n_scans) best = r
  }
  if (best === null || best.n_scans <= def.n_scans) return null
  return { label: recipeLabel(best.recipe_key), nScans: best.n_scans }
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
