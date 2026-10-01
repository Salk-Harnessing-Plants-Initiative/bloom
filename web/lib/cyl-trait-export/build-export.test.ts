/**
 * The batched export build (design D2, D3; tasks.md 5.1) against a fake that serves
 * golden/input.json verbatim. Outputs must equal the hand-written golden files.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { fakeDb, RECORDED, type FakeOptions } from './__fixtures__/fake-db'
import { buildExport, listMergedRecipes, resolveSelection, type BuildOptions } from './build-export'
import type { ExportDb } from './db'
import { csvRows } from './csv'
import { ExportError, SELECTION_CHANGED } from './errors'
import { BATCH_SCANS, LISTING_BATCH_SCANS } from './limits'
import type { Selection } from './selection'

const GOLDEN = join(__dirname, '__fixtures__', 'golden')
const golden = (name: string) => readFileSync(join(GOLDEN, name))
const { K, K2 } = RECORDED.input.keys
const AT = new Date('2026-10-02T12:00:00.000Z')
const WHOLE: Selection = { experiment: 1 }

const joinBytes = (xs: Iterable<Uint8Array>) => Buffer.concat([...xs].map((x) => Buffer.from(x)))

async function run(
  sel: Selection,
  opts: Partial<BuildOptions> & { recipeKey: string },
  fake: FakeOptions = {}
) {
  const f = fakeDb(fake)
  const go = async () => {
    const resolved = await resolveSelection(f.db, sel)
    return buildExport(f.db, resolved, sel, {
      chosen: 'user',
      generatedAt: AT,
      version: '1.0.0',
      batchSize: 100,
      ...opts,
    })
  }
  const p = go()
  if (fake.order || fake.hold) void f.drain()
  return { f, result: await p }
}

async function failure(
  sel: Selection,
  opts: Partial<BuildOptions> & { recipeKey: string },
  fake: FakeOptions
) {
  const f = fakeDb(fake)
  const resolved = await resolveSelection(fakeDb().db, sel)
  const err = await buildExport(f.db, resolved, sel, {
    chosen: 'user',
    generatedAt: AT,
    version: '1.0.0',
    batchSize: 2,
    ...opts,
  }).catch((e) => e)
  return { f, err }
}

describe('(a) outputs equal the golden files', () => {
  const cases = [
    [K, 'K'],
    ['legacy:9', 'legacy-9'],
    ['unattributed', 'unattributed'],
  ] as const
  const variants: [number, FakeOptions][] = [
    [1, {}],
    [2, {}],
    [100, {}],
    [2, { order: 'reverse' }],
    [1, { order: 'random', shuffleRows: true }],
    [2, { order: 'random', shuffleRows: true }],
  ]
  for (const [key, stem] of cases) {
    it.each(variants)(`${stem} at batch %i with %j`, async (batchSize, fake) => {
      const { result } = await run(WHOLE, { recipeKey: key, batchSize }, fake)
      expect(joinBytes(result.csv()).equals(golden(`${stem}.csv`))).toBe(true)
      expect(result.sidecarJson).toBe(golden(`${stem}.export.json`).toString('utf8'))
      expect(result.excludedCsv).toBe(golden(`${stem}.excluded.csv`).toString('utf8'))
    })
  }

  it('names the files by the stem', async () => {
    const { result } = await run(WHOLE, { recipeKey: K })
    expect(result.stem).toBe('fixture-diversity-screen_1bad3d73_20261002')
  })
})

describe('(b) call shapes', () => {
  it('passes the experiment, a bounded non-empty scan_ids and the explicit key on every call', async () => {
    const { f } = await run(WHOLE, { recipeKey: K, batchSize: 2 })
    const rpc = f.calls.filter((c) => ['listRecipes', 'coverage', 'traits'].includes(c.method))
    expect(rpc.length).toBeGreaterThan(0)
    for (const c of rpc) {
      expect(c.args[0]).toBe(1)
      const ids = (c.method === 'traits' ? c.args[2] : c.args[1]) as number[]
      expect(ids.length).toBeGreaterThan(0)
      expect(ids.length).toBeLessThanOrEqual(c.method === 'listRecipes' ? LISTING_BATCH_SCANS : 2)
      if (c.method === 'coverage') expect(c.args[2]).toBe(K)
      if (c.method === 'traits') expect(c.args[1]).toBe(K)
    }
  })

  it('lists recipes in one call for a selection within LISTING_BATCH_SCANS, whatever batchSize is', async () => {
    const { f } = await run(WHOLE, { recipeKey: K, batchSize: 1 })
    const all = RECORDED.input.chunk_listings['8'][0].scan_ids
    const listings = f.calls.filter((c) => c.method === 'listRecipes')
    expect(listings.map((c) => c.args[1])).toEqual([all])
  })

  it('makes no trait call for a batch with no included scans', async () => {
    const { f } = await run(WHOLE, { recipeKey: 'legacy:9', batchSize: 2 })
    const traitCalls = f.calls.filter((c) => c.method === 'traits')
    expect(traitCalls.map((c) => c.args[2])).toEqual([[102]])
  })
})

describe('(c) concurrency', () => {
  it('reaches exactly the concurrency limit and never exceeds it', async () => {
    const { f } = await run(WHOLE, { recipeKey: K, batchSize: 1 }, { hold: true })
    expect(f.peak).toBe(3)
  })
})

describe('(d) integrity and RPC failures produce no output', () => {
  const dropRow = (method: 'coverage' | 'traits') => ({
    [method]: (r: unknown) =>
      method === 'coverage'
        ? (r as { scan_id: number }[]).slice(1)
        : { ...(r as { rows: unknown[] }), rows: (r as { rows: unknown[] }).rows.slice(1) },
  })

  it.each([
    [
      'a missing coverage row',
      { tamper: { coverage: (r: unknown) => (r as unknown[]).slice(1) } },
      /no coverage row|coverage/,
    ],
    [
      'a coverage row outside the batch',
      {
        tamper: {
          coverage: (r: unknown) => [...(r as object[]), { ...(r as object[])[0], scan_id: 999 }],
        },
      },
      /coverage/,
    ],
    [
      'a truncated trait response',
      {
        tamper: {
          traits: (r: unknown) => ({ ...(r as object), count: (r as { count: number }).count + 1 }),
        },
      },
      /batch/,
    ],
    [
      'an included scan with no trait rows',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = (r as { rows: { scan_id: number }[] }).rows.filter((x) => x.scan_id !== 10)
            return { rows, count: rows.length }
          },
        },
      },
      /scan 10/,
    ],
    [
      'a row from another source',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = (r as { rows: { source_id: number }[] }).rows.map((x) => ({
              ...x,
              source_id: 999,
            }))
            return { rows, count: rows.length }
          },
        },
      },
      new RegExp(SELECTION_CHANGED.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    ],
    [
      'a row for a scan not requested',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = [
              ...(r as { rows: object[] }).rows,
              { scan_id: 101, trait_name: 'x', source_id: 21, trait_value: 1 },
            ]
            return { rows, count: rows.length }
          },
        },
      },
      /scan 101/,
    ],
    [
      'a repeated scan and trait',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = (r as { rows: object[] }).rows
            return { rows: [...rows, rows[0]], count: rows.length + 1 }
          },
        },
      },
      /more than once/,
    ],
    [
      'a trait named like a fixed column',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = (r as { rows: { trait_name: string }[] }).rows.map((x, i) =>
              i === 0 ? { ...x, trait_name: 'genotype' } : x
            )
            return { rows, count: rows.length }
          },
        },
      },
      /fixed column/,
    ],
    [
      'a non-numeric value',
      {
        tamper: {
          traits: (r: unknown) => {
            const rows = (r as { rows: object[] }).rows.map((x, i) =>
              i === 0 ? { ...x, trait_value: 'oops' } : x
            )
            return { rows, count: rows.length }
          },
        },
      },
      /not a number/,
    ],
    [
      'a missing accession row',
      { tamper: { accessions: (r: unknown) => (r as unknown[]).slice(1) } },
      /accession/,
    ],
    [
      'a missing source row',
      { tamper: { sourceMetadata: (r: unknown) => (r as unknown[]).slice(1) } },
      /source/,
    ],
  ] as [string, FakeOptions, RegExp][])('%s', async (_name, fake, detail) => {
    const { err } = await failure(WHOLE, { recipeKey: K }, fake)
    expect(err).toBeInstanceOf(ExportError)
    expect((err as ExportError).detail).toMatch(detail)
  })

  it('reports an included-count drift as a changed selection', async () => {
    const { err } = await failure(
      WHOLE,
      { recipeKey: K },
      {
        tamper: {
          listRecipes: (r: unknown) =>
            (r as { recipe_key: string; n_scans: number }[]).map((x) =>
              x.recipe_key === K ? { ...x, n_scans: x.n_scans - 1 } : x
            ),
        },
      }
    )
    expect((err as ExportError).kind).toBe('selection_changed')
    expect((err as ExportError).detail).toBe(SELECTION_CHANGED)
  })

  it.each([
    ['57014', /timed out \(57014\) in batch 2 of 4; try a wave or age filter/],
    ['PGRST202', /^export not available yet$/],
    ['PGRST301', /^session expired; sign in again$/],
  ])('maps RPC error %s to a fixed detail', async (code, detail) => {
    const { err } = await failure(
      WHOLE,
      { recipeKey: K },
      { failOn: { method: 'coverage', nth: 2, error: { code, message: 'RAW-POSTGREST-TEXT' } } }
    )
    expect(err).toBeInstanceOf(ExportError)
    expect((err as ExportError).detail).toMatch(detail)
    expect((err as ExportError).detail).not.toContain('RAW-POSTGREST-TEXT')
  })
})

describe('(e)-(j) recipe choice, scan grain, abort and provenance', () => {
  it('(e) fails a key absent from the selection before any coverage call', async () => {
    const f = fakeDb()
    const sel: Selection = { experiment: 1, wave: 2 }
    const resolved = await resolveSelection(f.db, sel)
    const err = await buildExport(f.db, resolved, sel, {
      recipeKey: K,
      chosen: 'user',
      generatedAt: AT,
      version: '1.0.0',
    }).catch((e) => e)
    expect((err as ExportError).kind).toBe('not_in_selection')
    expect((err as ExportError).detail).toBe('this recipe is not in the selection')
    expect(f.calls.some((c) => c.method === 'coverage')).toBe(false)
  })

  it('(f) records chosen_by default only for the current default', async () => {
    const moved = await run(WHOLE, { recipeKey: K, chosen: 'default' })
    expect(JSON.parse(moved.result.sidecarJson).recipe.chosen_by).toBe('user')
    const kept = await run(WHOLE, { recipeKey: K2, chosen: 'default' })
    expect(JSON.parse(kept.result.sidecarJson).recipe.chosen_by).toBe('default')
  })

  it('(g) a scan export agrees with the experiment export by column', async () => {
    const whole = await run(WHOLE, { recipeKey: K })
    const one = await run({ scan: 100 }, { recipeKey: K })
    const parse = (bytes: Buffer) =>
      bytes
        .toString('utf8')
        .trimEnd()
        .split('\r\n')
        .map((l) => l.split(','))
    const [wh, ...wrows] = parse(joinBytes(whole.result.csv()))
    const [sh, srow] = parse(joinBytes(one.result.csv()))
    const wrow = wrows.find((r) => r[0] === '100')!
    sh.forEach((col, i) => expect(srow[i]).toBe(wrow[wh.indexOf(col)]))
    wh.filter((c) => !sh.includes(c)).forEach((c) => expect(wrow[wh.indexOf(c)]).toBe(''))
    const side = JSON.parse(one.result.sidecarJson)
    expect(side.selection.scan_ids).toEqual([100])
    expect(side.selection.filters).toEqual({ scan_id: 100 })
    expect(one.result.stem).toBe('fixture-diversity-screen_scan100_1bad3d73_20261002')
  })

  it('(h) stops starting batches once aborted', async () => {
    const ctrl = new AbortController()
    const f = fakeDb({ hold: true })
    const resolved = await resolveSelection(fakeDb().db, WHOLE)
    const p = buildExport(f.db, resolved, WHOLE, {
      recipeKey: K,
      chosen: 'user',
      generatedAt: AT,
      version: '1.0.0',
      batchSize: 1,
      signal: ctrl.signal,
    }).catch((e) => e)
    await new Promise((r) => setImmediate(r))
    const before = f.calls.length
    ctrl.abort()
    void f.drain()
    const err = await p
    expect((err as ExportError).kind).toBe('cancelled')
    expect(f.calls.length).toBe(before)
  })

  it('(i) never exports the superseded source', async () => {
    const { result, f } = await run(WHOLE, { recipeKey: K })
    const side = JSON.parse(result.sidecarJson)
    expect(side.included.source_ids).toEqual([21, 22, 30])
    expect(joinBytes(result.csv()).toString('utf8')).not.toContain('lateral_extra_old')
    const metaCalls = f.calls.filter((c) => c.method === 'sourceMetadata')
    expect(metaCalls.flatMap((c) => c.args[0] as number[]).sort((a, b) => a - b)).toEqual([
      21, 22, 30,
    ])
  })

  it("(j) K's header has none of the crown traits", async () => {
    const { result } = await run(WHOLE, { recipeKey: K })
    const header = joinBytes(result.csv()).toString('utf8').split('\r\n')[0]
    expect(header).not.toMatch(/crown_/)
  })

  it('reports progress per batch', async () => {
    const seen: [string, number, number][] = []
    await run(WHOLE, {
      recipeKey: K,
      batchSize: 2,
      onProgress: (p) => seen.push([p.phase, p.done, p.total]),
    })
    expect(seen.filter(([p]) => p === 'traits').at(-1)).toEqual(['traits', 4, 4])
  })
})

describe('resolveSelection', () => {
  it('returns the ascending scans of a visible experiment', async () => {
    const r = await resolveSelection(fakeDb().db, { experiment: 1, age: 0 })
    expect(r.experiment).toEqual({ id: 1, name: 'Fixture Diversity Screen' })
    expect(r.scans.map((s) => s.scan_id)).toEqual([9, 102, 200])
  })

  it("resolves a scan's experiment", async () => {
    const r = await resolveSelection(fakeDb().db, { scan: 201 })
    expect(r.scans.map((s) => s.scan_id)).toEqual([201])
  })

  it.each([
    ['an invisible experiment', { experiment: 1 }, { experimentVisible: false }],
    ['an unknown scan', { scan: 12345 }, {}],
  ] as [string, Selection, FakeOptions][])('is not found for %s', async (_n, sel, fake) => {
    const err = await resolveSelection(fakeDb(fake).db, sel).catch((e) => e)
    expect((err as ExportError).kind).toBe('not_found')
  })

  it('tells an empty selection apart from a missing experiment', async () => {
    const err = await resolveSelection(fakeDb().db, { experiment: 1, wave: 9 }).catch((e) => e)
    expect((err as ExportError).kind).toBe('empty_selection')
  })

  it('reports a count mismatch as a changed selection', async () => {
    const err = await resolveSelection(
      fakeDb({ tamper: { countScans: (n: unknown) => (n as number) + 1 } }).db,
      WHOLE
    ).catch((e) => e)
    expect((err as ExportError).kind).toBe('selection_changed')
  })
})

// csvRows is re-exported for the zip and route layers; keep it imported so a rename breaks here.
void csvRows

describe('listMergedRecipes batching (tasks.md 7.4)', () => {
  // Staging, 2026-10-01: one list_trait_recipes call over experiment 1's 18,471 scans
  // took 1.26 s, while 185 calls of BATCH_SCANS would exceed Kong's 60 s per request
  // with two jobs running.
  const stubDb = () => {
    const sizes: number[] = []
    const db = {
      listRecipes: async (_e: number, ids: number[]) => {
        sizes.push(ids.length)
        return []
      },
    } as unknown as ExportDb
    return { db, sizes }
  }
  const ids = (n: number) => Array.from({ length: n }, (_, i) => i + 1)

  it('is larger than BATCH_SCANS and covers the largest experiment (18,471 scans)', () => {
    expect(LISTING_BATCH_SCANS).toBeGreaterThan(BATCH_SCANS)
    expect(LISTING_BATCH_SCANS).toBeGreaterThanOrEqual(18_471)
  })

  it('makes one call for a selection of up to LISTING_BATCH_SCANS scans by default', async () => {
    const { db, sizes } = stubDb()
    await listMergedRecipes(db, 1, ids(BATCH_SCANS * 2 + 50))
    expect(sizes).toEqual([BATCH_SCANS * 2 + 50])
  })

  it('takes the listing batch size as listingBatchSize, as BuildOptions does (tasks.md 10a.5)', async () => {
    const { db, sizes } = stubDb()
    await listMergedRecipes(db, 1, ids(5), { listingBatchSize: 2 })
    expect(sizes).toEqual([2, 2, 1])
  })

  it('splits a larger selection at LISTING_BATCH_SCANS', async () => {
    const { db, sizes } = stubDb()
    await listMergedRecipes(db, 1, ids(LISTING_BATCH_SCANS + 1))
    expect(sizes).toEqual([LISTING_BATCH_SCANS, 1])
  })
})
