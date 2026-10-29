/**
 * Merging per-batch recipe listings (design D2 step 3). The rows are the real
 * list_trait_recipes output recorded in golden/input.json, so "merged equals one
 * call" is checked against the SQL's own single-call listing.
 */

import { describe, expect, it } from 'vitest'

import input from './__fixtures__/golden/input.json'
import { ExportError } from './errors'
import { mergeRecipeListings, resolveChosenBy, type ListingChunk, type RecipeRow } from './recipes'

type Recorded = { scan_ids: number[]; rows: RecipeRow[] }
const listings = input.chunk_listings as unknown as Record<string, Recorded[]>
const chunks = (size: string): ListingChunk[] =>
  listings[size].map((c) => ({ scanIds: c.scan_ids, rows: c.rows }))
const whole = listings['8'][0].rows
const { K, K2 } = input.keys

describe('mergeRecipeListings', () => {
  it.each(['1', '2', '8'])('merges chunks of size %s into the single-call listing', (size) => {
    expect(mergeRecipeListings(chunks(size))).toEqual(whole)
  })

  it('sums n_scans and takes the newest source across chunks', () => {
    const merged = mergeRecipeListings(chunks('1'))
    const k = merged.find((r) => r.recipe_key === K)!
    expect(k.n_scans).toBe(3)
    expect(k.newest_source_id).toBe(30)
  })

  it('orders by newest_source_id descending with unattributed last, and marks one default', () => {
    const merged = mergeRecipeListings(chunks('2'))
    expect(merged.map((r) => r.recipe_key)).toEqual([K2, K, 'legacy:9', 'unattributed'])
    expect(merged.filter((r) => r.is_default).map((r) => r.recipe_key)).toEqual([K2])
  })

  it('takes version, kind and definition from the chunk holding the newest source', () => {
    const newer: RecipeRow = {
      recipe_key: 'x',
      recipe_key_version: 1,
      recipe_kind: 'pipeline',
      definition: { v: 'new' },
      n_scans: 1,
      newest_source_id: 50,
      is_default: true,
    }
    const older: RecipeRow = { ...newer, definition: { v: 'old' }, newest_source_id: 10 }
    const merged = mergeRecipeListings([
      { scanIds: [1], rows: [older] },
      { scanIds: [2], rows: [newer] },
    ])
    expect(merged).toEqual([{ ...newer, n_scans: 2, is_default: true }])
  })

  it('returns no rows, and so no default, for no recipes', () => {
    expect(mergeRecipeListings([{ scanIds: [1], rows: [] }])).toEqual([])
    expect(mergeRecipeListings([])).toEqual([])
  })

  it('rejects a chunk reporting more scans than it holds', () => {
    const bad = chunks('2')
    bad[0] = { ...bad[0], rows: bad[0].rows.map((r) => ({ ...r, n_scans: 3 })) }
    expect(() => mergeRecipeListings(bad)).toThrow(ExportError)
  })
})

describe('resolveChosenBy', () => {
  it.each([
    ['default', K2, 'default'],
    ['default', K, 'user'],
    ['user', K2, 'user'],
    ['user', K, 'user'],
  ] as const)('hint %s for %s records %s', (hint, key, out) => {
    expect(resolveChosenBy(hint, key, whole)).toBe(out)
  })
})
