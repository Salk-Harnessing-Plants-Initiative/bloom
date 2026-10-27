/** The export sidecar and excluded CSV (design D5; spec "Trait export file conventions"). */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import input from './__fixtures__/golden/input.json'
import { ExportError } from './errors'
import { excludedCsv } from './excluded'
import type { RecipeRow } from './recipes'
import { scanIdsSha256 } from './selection'
import {
  buildSidecar,
  generatorVersion,
  serializeSidecar,
  type CoverageRow,
  type SourceMeta,
} from './sidecar'

const GOLDEN = join(__dirname, '__fixtures__', 'golden')
const golden = (name: string) => readFileSync(join(GOLDEN, name), 'utf8')

type Rec = { recipe_key: string; coverage: CoverageRow[] }
const recipes = input.recipes as unknown as Record<string, Rec>
const merged = (input.chunk_listings as unknown as Record<string, { rows: RecipeRow[] }[]>)['8'][0]
  .rows
const sources = new Map(
  (input.sources as unknown as { id: number; metadata: SourceMeta | null }[]).map((s) => [
    s.id,
    s.metadata,
  ])
)
const ALL = [9, 10, 100, 101, 102, 103, 200, 201]
const AT = new Date('2026-10-02T12:00:00.000Z')

function build(label: string, over: Partial<Parameters<typeof buildSidecar>[0]> = {}) {
  const rec = recipes[label]
  return buildSidecar({
    generatedAt: AT,
    version: '1.0.0',
    experimentId: 1,
    selection: { experiment: 1 },
    selectedIds: ALL,
    recipeKey: rec.recipe_key,
    chosenBy: 'user',
    merged,
    coverage: rec.coverage,
    sourceMetadata: sources,
    ...over,
  })
}

describe('buildSidecar', () => {
  it.each([
    ['K', 'K.export.json'],
    ['legacy:9', 'legacy-9.export.json'],
    ['unattributed', 'unattributed.export.json'],
  ])('reproduces the golden %s sidecar byte for byte', (label, file) => {
    expect(serializeSidecar(build(label))).toBe(golden(file))
  })

  it('accounts for every selected scan exactly once', () => {
    for (const label of ['K', 'legacy:9', 'unattributed']) {
      const s = build(label)
      const ids = [...s.excluded.map((e) => e.scan_id)]
      expect(s.included.n_scans + s.excluded.length).toBe(ALL.length)
      expect(new Set(ids).size).toBe(ids.length)
    }
  })

  it('lists distinct ascending source ids, [] for unattributed and [9] for legacy', () => {
    expect(build('K').included.source_ids).toEqual([21, 22, 30])
    expect(build('legacy:9').included.source_ids).toEqual([9])
    expect(build('unattributed').included.source_ids).toEqual([])
  })

  it('records other recipes as {recipe_key, n_scans} in listing order', () => {
    const others = build('K').other_recipes_in_selection
    expect(others.map((o) => Object.keys(o))).toEqual(others.map(() => ['recipe_key', 'n_scans']))
    expect(others.map((o) => o.recipe_key)).toEqual([input.keys.K2, 'legacy:9', 'unattributed'])
  })

  it('has observed only for pipeline recipes, from the included sources only', () => {
    const k = build('K')
    expect(k.recipe.observed?.sleap_nn_versions).toEqual(['0.1.0'])
    expect(k.recipe.observed?.contract_versions).toEqual(['0.1.0a9'])
    expect(build('legacy:9').recipe).not.toHaveProperty('observed')
    expect(build('unattributed').recipe).not.toHaveProperty('observed')
  })

  it('serializes free-form objects with sorted keys, whatever their input order', () => {
    const shuffled = merged.map((r) =>
      r.recipe_key === input.keys.K
        ? {
            ...r,
            definition: {
              traits_code_sha: 'def4567',
              predict_code_sha: 'abc1234',
              models: (r.definition as { models: unknown }).models,
            },
          }
        : r
    )
    expect(serializeSidecar(build('K', { merged: shuffled }))).toBe(golden('K.export.json'))
  })

  it('records a filtered selection with its ids, hash and filters', () => {
    const ids = [9, 102, 200]
    const cov = recipes.K.coverage.filter((c) => ids.includes(c.scan_id))
    const s = build('K', {
      selection: { experiment: 1, wave: 0, age: 0 },
      selectedIds: ids,
      coverage: cov,
    })
    expect(s.selection).toEqual({
      experiment_ids: [1],
      scan_ids: [9, 102, 200],
      filters: { wave_number: 0, plant_age_days: 0 },
      scan_ids_sha256: scanIdsSha256(ids),
    })
  })

  it('writes generated_at as toISOString()', () => {
    expect(build('K').generated_at).toBe('2026-10-02T12:00:00.000Z')
  })

  it('rejects coverage that misses or adds a selected scan', () => {
    const cov = recipes.K.coverage
    expect(() => build('K', { coverage: cov.slice(1) })).toThrow(ExportError)
    expect(() => build('K', { selectedIds: ALL.slice(1) })).toThrow(ExportError)
  })
})

describe('generatorVersion', () => {
  it('is the package version, plus the build sha only when set', () => {
    expect(generatorVersion('1.0.0', undefined)).toBe('1.0.0')
    expect(generatorVersion('1.0.0', '')).toBe('1.0.0')
    expect(generatorVersion('1.0.0', 'abc1234')).toBe('1.0.0+abc1234')
  })
})

describe('excludedCsv', () => {
  it.each([
    ['K', 'K.excluded.csv'],
    ['legacy:9', 'legacy-9.excluded.csv'],
    ['unattributed', 'unattributed.excluded.csv'],
  ])('reproduces the golden %s excluded CSV', (label, file) => {
    expect(excludedCsv(recipes[label].coverage)).toBe(golden(file))
  })

  it('is header-only when nothing is excluded', () => {
    const allIncluded = recipes.K.coverage.filter((c) => c.status === 'included')
    expect(excludedCsv(allIncluded)).toBe('scan_id,plant_qr_code,reason,available_recipes\r\n')
  })
})
