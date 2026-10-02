/** What the dialog shows for a listing (design D8; spec "Trait download dialog recipe list"). */

import { describe, expect, it } from 'vitest'

import type { RecipeRow } from '../recipes'
import {
  NO_RECIPES,
  NO_SCANS,
  describeRecipe,
  emptyState,
  fewerScansNote,
  prefill,
  recipeLabel,
} from './recipe-view'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const K2 = 'b03e1092' + '0'.repeat(56)

function row(over: Partial<RecipeRow> & Pick<RecipeRow, 'recipe_key'>): RecipeRow {
  return {
    recipe_key_version: null,
    recipe_kind: 'legacy',
    definition: null,
    n_scans: 1,
    newest_source_id: 1,
    is_default: false,
    ...over,
  }
}

describe('recipeLabel', () => {
  it('is the key segment of each kind of key', () => {
    expect(recipeLabel(K)).toBe('1bad3d73')
    expect(recipeLabel('legacy:5')).toBe('legacy-5')
    expect(recipeLabel('unattributed')).toBe('unattributed')
  })

  it('falls back to the raw key instead of throwing on a key it does not know', () => {
    expect(recipeLabel('recipe-v2:abc')).toBe('recipe-v2:abc')
  })
})

describe('describeRecipe', () => {
  it('gives a pipeline recipe its models (name and version) and short code SHAs', () => {
    expect(
      describeRecipe({
        recipe_kind: 'pipeline',
        definition: {
          models: [
            ['lateral-root', 'v3', 'sha256:lat3'],
            ['primary-root', 'v2', 'sha256:pri2'],
          ],
          predict_code_sha: 'abc1234def5678abc1234def5678abc1234def56',
          traits_code_sha: 'def4567',
        },
      })
    ).toEqual(['Models: lateral-root v3, primary-root v2', 'Code: predict abc1234, traits def4567'])
  })

  it('leaves out the fields a hand-built pipeline source lacks', () => {
    expect(
      describeRecipe({ recipe_kind: 'pipeline', definition: { traits_code_sha: 'def4567' } })
    ).toEqual(['Code: traits def4567'])
    expect(describeRecipe({ recipe_kind: 'pipeline', definition: {} })).toEqual([])
    expect(describeRecipe({ recipe_kind: 'pipeline', definition: null })).toEqual([])
    expect(
      describeRecipe({
        recipe_kind: 'pipeline',
        definition: { models: 'not a list', predict_code_sha: 7 },
      })
    ).toEqual([])
  })

  it('gives a legacy recipe its source name', () => {
    expect(
      describeRecipe({
        recipe_kind: 'legacy',
        definition: { source_id: 9, source_name: 'legacy-fixture-9' },
      })
    ).toEqual(['Source: legacy-fixture-9'])
    expect(describeRecipe({ recipe_kind: 'legacy', definition: { source_id: 9 } })).toEqual([])
  })

  it('says an unattributed recipe has no source', () => {
    expect(describeRecipe({ recipe_kind: 'unattributed', definition: null })).toEqual([
      'no source recorded',
    ])
  })
})

describe('fewerScansNote', () => {
  it('names the recipe covering the most scans when the default covers fewer', () => {
    const rows = [
      row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 3, is_default: true }),
      row({ recipe_key: 'legacy:5', n_scans: 60 }),
    ]
    expect(fewerScansNote(rows)).toEqual({ label: 'legacy-5', nScans: 60 })
  })

  it('gives no note when the default already covers the most scans', () => {
    expect(
      fewerScansNote([
        row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 60, is_default: true }),
        row({ recipe_key: 'legacy:5', n_scans: 60 }),
        row({ recipe_key: 'legacy:4', n_scans: 10 }),
      ])
    ).toBeNull()
    expect(
      fewerScansNote([
        row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 3, is_default: true }),
      ])
    ).toBeNull()
  })

  it('names the first in listing order on a tie', () => {
    const rows = [
      row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 3, is_default: true }),
      row({ recipe_key: K2, recipe_kind: 'pipeline', n_scans: 40 }),
      row({ recipe_key: 'legacy:5', n_scans: 40 }),
    ]
    expect(fewerScansNote(rows)).toEqual({ label: 'b03e1092', nScans: 40 })
  })

  it('never names unattributed', () => {
    expect(
      fewerScansNote([
        row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 3, is_default: true }),
        row({
          recipe_key: 'unattributed',
          recipe_kind: 'unattributed',
          n_scans: 90,
          newest_source_id: null,
        }),
      ])
    ).toBeNull()
    expect(
      fewerScansNote([
        row({ recipe_key: K, recipe_kind: 'pipeline', n_scans: 3, is_default: true }),
        row({ recipe_key: 'legacy:5', n_scans: 20 }),
        row({
          recipe_key: 'unattributed',
          recipe_kind: 'unattributed',
          n_scans: 90,
          newest_source_id: null,
        }),
      ])
    ).toEqual({ label: 'legacy-5', nScans: 20 })
  })

  it('gives no note for an empty listing', () => {
    expect(fewerScansNote([])).toBeNull()
  })
})

describe('prefill', () => {
  it('keeps a value the loaded list has, including 0', () => {
    expect(prefill(2, [1, 2, 3])).toBe(2)
    expect(prefill(0, [0, 7])).toBe(0)
  })

  it('opens as "All" for a value the list lacks, no value, or an empty list', () => {
    expect(prefill(4, [1, 2, 3])).toBe('all')
    expect(prefill(undefined, [1, 2])).toBe('all')
    expect(prefill(0, [])).toBe('all')
  })
})

describe('emptyState', () => {
  it('says no scans match when nothing is selected', () => {
    expect(emptyState(0, [])).toBe(NO_SCANS)
    expect(NO_SCANS).toBe('No scans match this wave and age')
  })

  it('says there are no trait results when scans have no recipes', () => {
    expect(emptyState(12, [])).toBe(NO_RECIPES)
    expect(NO_RECIPES).toBe('No trait results for this selection')
  })

  it('is null when there is something to download', () => {
    expect(emptyState(12, [row({ recipe_key: 'legacy:5', is_default: true })])).toBeNull()
  })
})
