/**
 * The supabase-js adapter behind ExportDb: query shapes, the verified user's token,
 * abort signals, the shared semaphore, and PostgREST errors reduced to {code, message}.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Op = [string, ...unknown[]]
type Response = {
  data?: unknown
  error?: { code: string; message: string } | null
  count?: number | null
}

const recorded: { root: string; ops: Op[] }[] = []
let respond: (root: string, ops: Op[]) => Response = () => ({ data: [], error: null })
let inFlight = 0
let peak = 0
const created: unknown[][] = []

function builder(root: string) {
  const ops: Op[] = []
  recorded.push({ root, ops })
  const b: Record<string, unknown> = {}
  for (const m of [
    'select',
    'eq',
    'gt',
    'in',
    'order',
    'limit',
    'maybeSingle',
    'abortSignal',
    'retry',
  ]) {
    b[m] = (...args: unknown[]) => {
      ops.push([m, ...args])
      return b
    }
  }
  b.then = (resolve: (r: Response) => void, reject: (e: unknown) => void) => {
    inFlight += 1
    peak = Math.max(peak, inFlight)
    return new Promise<Response>((r) => setImmediate(() => r(respond(root, ops))))
      .finally(() => {
        inFlight -= 1
      })
      .then(resolve, reject)
  }
  return b
}

vi.mock('@supabase/supabase-js', () => ({
  createClient: (...args: unknown[]) => {
    created.push(args)
    return {
      from: (table: string) => builder(`from:${table}`),
      rpc: (fn: string, params: unknown, opts?: unknown) => {
        const b = builder(`rpc:${fn}`)
        ;(b as { _params?: unknown })._params = params
        recorded.at(-1)!.ops.push(['params', params, opts ?? null])
        return b
      },
    }
  },
}))

import { createExportDb } from './db'
import { resetExportStateForTests } from './state'

const opsOf = (root: string) => recorded.filter((r) => r.root === root).map((r) => r.ops)

beforeEach(() => {
  recorded.length = 0
  created.length = 0
  inFlight = 0
  peak = 0
  respond = () => ({ data: [], error: null })
  process.env.SUPABASE_URL = 'http://kong:8000'
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon'
})
afterEach(() => resetExportStateForTests())

describe('createExportDb', () => {
  it('builds its client on the captured token, with no auth session of its own', async () => {
    createExportDb('the-user-token')
    const [url, key, options] = created[0] as [
      string,
      string,
      { accessToken: () => Promise<string> },
    ]
    expect(url).toBe('http://kong:8000')
    expect(key).toBe('anon')
    await expect(options.accessToken()).resolves.toBe('the-user-token')
  })

  it('reads a visible experiment, or null', async () => {
    const db = createExportDb('t')
    respond = () => ({ data: { id: 1, name: 'E' }, error: null })
    await expect(db.experiment(1)).resolves.toEqual({ id: 1, name: 'E' })
    respond = () => ({ data: null, error: null })
    await expect(db.experiment(2)).resolves.toBeNull()
    expect(opsOf('from:cyl_experiments')[0]).toEqual(
      expect.arrayContaining([['select', 'id,name'], ['eq', 'id', 1], ['maybeSingle']])
    )
  })

  it('pages the selection by keyset in scan_id order', async () => {
    const db = createExportDb('t')
    await db.pageScans({ experimentId: 1, wave: 0, age: 0 }, 42, 1000)
    expect(opsOf('from:cyl_scans_extended')[0]).toEqual(
      expect.arrayContaining([
        ['select', '*'],
        ['eq', 'experiment_id', 1],
        ['eq', 'wave_number', 0],
        ['eq', 'plant_age_days', 0],
        ['gt', 'scan_id', 42],
        ['order', 'scan_id', { ascending: true }],
        ['limit', 1000],
      ])
    )
  })

  it('omits the keyset bound on the first page and filters a scan by id', async () => {
    const db = createExportDb('t')
    await db.pageScans({ scanId: 7 }, null, 1000)
    const ops = opsOf('from:cyl_scans_extended')[0]
    expect(ops).toContainEqual(['eq', 'scan_id', 7])
    expect(ops.some((o) => o[0] === 'gt')).toBe(false)
  })

  it('counts the selection exactly, without rows', async () => {
    const db = createExportDb('t')
    respond = () => ({ data: null, error: null, count: 8 })
    await expect(db.countScans({ experimentId: 1 })).resolves.toBe(8)
    expect(opsOf('from:cyl_scans_extended')[0]).toContainEqual([
      'select',
      'scan_id',
      { count: 'exact', head: true },
    ])
  })

  it('calls the recipe RPCs with the experiment, the scan ids and the explicit key', async () => {
    const db = createExportDb('t')
    await db.listRecipes(1, [9, 10])
    await db.coverage(1, [9, 10], 'k')
    expect(opsOf('rpc:list_trait_recipes')[0]).toContainEqual([
      'params',
      { experiment_ids_: [1], scan_ids_: [9, 10] },
      null,
    ])
    expect(opsOf('rpc:get_trait_recipe_coverage')[0]).toContainEqual([
      'params',
      { experiment_ids_: [1], scan_ids_: [9, 10], recipe_key_: 'k' },
      null,
    ])
  })

  it('reads 4 trait columns with an exact count', async () => {
    const db = createExportDb('t')
    respond = () => ({ data: [{ scan_id: 9 }], error: null, count: 1 })
    await expect(db.traits(1, 'k', [9])).resolves.toEqual({ rows: [{ scan_id: 9 }], count: 1 })
    const ops = opsOf('rpc:get_experiment_traits')[0]
    expect(ops).toContainEqual([
      'params',
      { experiment_id_: 1, recipe_key_: 'k', scan_ids_: [9] },
      { count: 'exact' },
    ])
    expect(ops).toContainEqual(['select', 'scan_id,trait_name,source_id,trait_value'])
  })

  it('reads accessions and source metadata by id', async () => {
    const db = createExportDb('t')
    await db.accessions([1, 2])
    await db.sourceMetadata([21])
    expect(opsOf('from:accessions')[0]).toEqual(
      expect.arrayContaining([
        ['select', 'id,name'],
        ['in', 'id', [1, 2]],
      ])
    )
    expect(opsOf('from:cyl_trait_sources')[0]).toEqual(
      expect.arrayContaining([
        ['select', 'id,metadata'],
        ['in', 'id', [21]],
      ])
    )
  })

  it('refuses an empty scan id list before any request', async () => {
    const db = createExportDb('t')
    await expect(db.listRecipes(1, [])).rejects.toThrow()
    await expect(db.coverage(1, [], 'k')).rejects.toThrow()
    await expect(db.traits(1, 'k', [])).rejects.toThrow()
    expect(recorded).toHaveLength(0)
  })

  it('passes an abort signal on every request', async () => {
    const db = createExportDb('t')
    const ctrl = new AbortController()
    await db.listRecipes(1, [9], ctrl.signal)
    await db.accessions([1])
    for (const r of recorded) {
      const sig = r.ops.find((o) => o[0] === 'abortSignal')
      expect(sig?.[1]).toBeInstanceOf(AbortSignal)
    }
  })

  it("turns off the client library's own retries on every request (tasks.md 10a.3)", async () => {
    // postgrest-js 2.106.2 retries GET/HEAD up to 3 times on network errors and
    // 503/520; spec "No retries" covers those too.
    const db = createExportDb('t')
    const q = { experimentId: 1 }
    await db.experiment(1)
    await db.scanExperiment(9)
    await db.pageScans(q, null, 10)
    await db.countScans(q)
    await db.accessions([1])
    await db.listRecipes(1, [9])
    await db.coverage(1, [9], 'k')
    await db.traits(1, 'k', [9])
    await db.sourceMetadata([5])
    expect(recorded).toHaveLength(9)
    for (const r of recorded) {
      expect(r.ops, r.root).toContainEqual(['retry', false])
    }
  })

  it('reduces a PostgREST error to {code, message}', async () => {
    const db = createExportDb('t')
    respond = () => ({ data: null, error: { code: '57014', message: 'canceling statement' } })
    await expect(db.listRecipes(1, [9])).rejects.toEqual({
      code: '57014',
      message: 'canceling statement',
    })
  })

  it('keeps every request under the shared semaphore', async () => {
    const db = createExportDb('t')
    await Promise.all(Array.from({ length: 10 }, (_, i) => db.accessions([i + 1])))
    expect(peak).toBe(3)
  })
})
