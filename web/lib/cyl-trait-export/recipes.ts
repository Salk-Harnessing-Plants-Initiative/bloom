/**
 * Merging per-batch `list_trait_recipes` results (design D2 step 3).
 *
 * The export lists recipes in batches of LISTING_BATCH_SCANS scans. One call covers
 * every current experiment (experiment 1's 18,471 scans took 1.26 s on staging), so
 * the merge runs only for a larger selection. It equals one call over the whole
 * selection: batches are disjoint and presence depends only on the scan, so
 * `n_scans` sums and `newest_source_id` is the max; a source row fixes its version,
 * kind and definition; and only `unattributed` has a NULL id, which ranks last
 * (M2:142 orders `newest_source_id DESC NULLS LAST`).
 */

import { ExportError } from './errors'

export type RecipeKind = 'pipeline' | 'legacy' | 'unattributed'

export type RecipeRow = {
  recipe_key: string
  recipe_key_version: number | null
  recipe_kind: RecipeKind
  definition: Record<string, unknown> | null
  n_scans: number
  newest_source_id: number | null
  is_default: boolean
}

export type ListingChunk = { scanIds: number[]; rows: RecipeRow[] }

export function mergeRecipeListings(chunks: ListingChunk[]): RecipeRow[] {
  const merged = new Map<string, RecipeRow>()
  for (const { scanIds, rows } of chunks) {
    for (const row of rows) {
      if (row.n_scans > scanIds.length) {
        throw new ExportError(
          'integrity',
          `a recipe listing reported ${row.n_scans} scans for a batch of ${scanIds.length}`
        )
      }
      const seen = merged.get(row.recipe_key)
      if (seen === undefined) {
        merged.set(row.recipe_key, { ...row })
        continue
      }
      seen.n_scans += row.n_scans
      const newest = row.newest_source_id
      if (newest !== null && (seen.newest_source_id === null || newest > seen.newest_source_id)) {
        seen.newest_source_id = newest
        seen.recipe_key_version = row.recipe_key_version
        seen.recipe_kind = row.recipe_kind
        seen.definition = row.definition
      }
    }
  }
  const ordered = [...merged.values()].sort((a, b) => {
    if (a.newest_source_id === null) return b.newest_source_id === null ? 0 : 1
    if (b.newest_source_id === null) return -1
    return b.newest_source_id - a.newest_source_id
  })
  return ordered.map((row, i) => ({ ...row, is_default: i === 0 }))
}

/** `chosen_by` is `default` only if the caller took the default and it still is (D3). */
export function resolveChosenBy(
  hint: 'default' | 'user',
  key: string,
  merged: RecipeRow[]
): 'default' | 'user' {
  const current = merged.find((r) => r.is_default)
  return hint === 'default' && current?.recipe_key === key ? 'default' : 'user'
}
