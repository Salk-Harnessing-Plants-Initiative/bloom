/**
 * GET /api/cyl/trait-export/recipes (design D3, D7; spec "Trait export recipe
 * listing"), plus the process-wide limit across two jobs and a listing (tasks 6.7).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fakeDb, RECORDED, type FakeOptions } from '@/lib/cyl-trait-export/__fixtures__/fake-db'
import { LISTING_BATCH_SCANS } from '@/lib/cyl-trait-export/limits'

vi.mock('@/lib/supabase/server', () => ({
  getSession: vi.fn(),
  createServerSupabaseClient: vi.fn(),
}))
// One scan per coverage/trait batch, so every job has several batches in flight and
// the process-wide limit is actually exercised (the fixture has 8 scans). Listings use
// LISTING_BATCH_SCANS, which is not mocked, so a listing is one listRecipes call.
vi.mock('@/lib/cyl-trait-export/limits', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/cyl-trait-export/limits')>()),
  BATCH_SCANS: 1,
}))
vi.mock('@/lib/cyl-trait-export/db', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/cyl-trait-export/db')>()),
  createExportDb: vi.fn(),
}))

import * as routeModule from '@/app/api/cyl/trait-export/recipes/route'
import * as jobsRoute from '@/app/api/cyl/trait-export/jobs/route'
import { createExportDb, limitedDb } from '@/lib/cyl-trait-export/db'
import { resetExportStateForTests } from '@/lib/cyl-trait-export/state'
import { createServerSupabaseClient, getSession } from '@/lib/supabase/server'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const K2 = '2dde535d26774eedab8f04f10814d33933136c41d35013e32abe39000432ba96'

const b64url = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url')
const token = (sub: string, secondsLeft = 3600) =>
  `${b64url({ alg: 'HS256' })}.${b64url({
    sub,
    exp: Math.floor(Date.now() / 1000) + secondsLeft,
  })}.sig`

let getUser: ReturnType<typeof vi.fn>
let fake: ReturnType<typeof fakeDb>
function signIn(sub = 'user-1', secondsLeft = 3600) {
  vi.mocked(getSession).mockResolvedValue({ access_token: token(sub, secondsLeft) } as never)
  // Like GoTrue, answer for whoever the token names, so concurrent requests stay distinct.
  getUser.mockImplementation(async (jwt: string) => ({
    data: { user: { id: JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString()).sub } },
    error: null,
  }))
}
function useFake(opts: FakeOptions = {}) {
  fake = fakeDb(opts)
  vi.mocked(createExportDb).mockReturnValue(limitedDb(fake.db))
}

const list = (query: string, init: RequestInit = {}) =>
  routeModule.GET(
    new Request(`http://localhost/api/cyl/trait-export/recipes?${query}`, {
      headers: { 'sec-fetch-site': 'same-origin' },
      ...init,
    })
  )

beforeEach(() => {
  getUser = vi.fn()
  vi.mocked(createServerSupabaseClient).mockResolvedValue({ auth: { getUser } } as never)
  signIn()
  useFake()
})
afterEach(() => {
  vi.clearAllMocks()
  resetExportStateForTests()
})

describe('module contract and guards', () => {
  it('is a dynamic node route that refuses HEAD', async () => {
    expect(routeModule.dynamic).toBe('force-dynamic')
    expect(routeModule.runtime).toBe('nodejs')
    expect((await routeModule.HEAD()).status).toBe(405)
  })

  it('403 cross-site, 401 unverified, 503 when GoTrue fails', async () => {
    expect(
      (await list('experiment=1', { headers: { 'sec-fetch-site': 'cross-site' } })).status
    ).toBe(403)
    // Spec: any Sec-Fetch-Site other than same-origin is 403 (10a.6c).
    for (const site of ['same-site', 'none']) {
      expect((await list('experiment=1', { headers: { 'sec-fetch-site': site } })).status).toBe(403)
    }
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 401, message: 'x' } })
    expect((await list('experiment=1')).status).toBe(401)
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 500, message: 'x' } })
    expect((await list('experiment=1')).status).toBe(503)
    // auth-js reports a network failure as AuthRetryableFetchError with status 0 (tasks.md 10a.2).
    getUser.mockResolvedValue({
      data: { user: null },
      error: { name: 'AuthRetryableFetchError', status: 0, message: 'fetch failed' },
    })
    expect((await list('experiment=1')).status).toBe(503)
    expect(fake.calls).toEqual([])
  })

  it('has no session floor and needs no recipe or chosen', async () => {
    signIn('user-1', 100)
    expect((await list('experiment=1')).status).toBe(200)
  })

  it.each(['', 'experiment=0', 'experiment=1&scan=2', 'scan=5&age=0', 'experiment=1&age=07'])(
    '422 for %j',
    async (q) => {
      expect((await list(q)).status).toBe(422)
      expect(fake.calls).toEqual([])
    }
  )
})

describe('listing', () => {
  it('returns n_selected and the merged rows for a whole experiment', async () => {
    const res = await list('experiment=1')
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.n_selected).toBe(8)
    expect(body.rows.map((r: { recipe_key: string }) => r.recipe_key)).toEqual([
      K2,
      K,
      'legacy:9',
      'unattributed',
    ])
    expect(body.rows.filter((r: { is_default: boolean }) => r.is_default)).toHaveLength(1)
    expect(Object.keys(body.rows[0]).sort()).toEqual(
      [
        'definition',
        'is_default',
        'n_scans',
        'newest_source_id',
        'recipe_key',
        'recipe_key_version',
        'recipe_kind',
      ].sort()
    )
  })

  it("returns each recipe's definition, so the dialog can say what a recipe is", async () => {
    const body = await (await list('experiment=1')).json()
    const recorded = RECORDED.input.chunk_listings['8'][0].rows as {
      recipe_key: string
      definition: unknown
    }[]
    for (const row of body.rows as { recipe_key: string; definition: unknown }[]) {
      expect(row.definition).toEqual(
        recorded.find((r) => r.recipe_key === row.recipe_key)!.definition
      )
    }
    const byKind = Object.fromEntries(
      (body.rows as { recipe_kind: string; definition: unknown }[]).map((r) => [
        r.recipe_kind,
        r.definition,
      ])
    )
    expect(byKind.pipeline).toMatchObject({
      models: expect.any(Array),
      traits_code_sha: expect.any(String),
    })
    expect(byKind.legacy).toMatchObject({
      source_id: expect.any(Number),
      source_name: expect.any(String),
    })
    expect(byKind.unattributed).toBeNull()
  })

  it('passes the experiment and a bounded, non-empty scan list on every call', async () => {
    await list('experiment=1')
    const calls = fake.calls.filter((c) => c.method === 'listRecipes')
    expect(calls.length).toBeGreaterThan(0)
    for (const c of calls) {
      expect(c.args[0]).toBe(1)
      const ids = c.args[1] as number[]
      expect(ids.length).toBeGreaterThan(0)
      expect(ids.length).toBeLessThanOrEqual(LISTING_BATCH_SCANS)
    }
  })

  it('applies wave and age=0', async () => {
    const wave = await (await list('experiment=1&wave=2')).json()
    expect(wave.n_selected).toBe(2)
    expect(wave.rows.map((r: { recipe_key: string }) => r.recipe_key)).toEqual([K2])
    const age = await (await list('experiment=1&age=0')).json()
    expect(age.n_selected).toBe(3)
    expect(age.rows.find((r: { is_default: boolean }) => r.is_default).recipe_key).toBe(K)
  })

  it('answers an empty selection with n_selected 0 and no rows', async () => {
    const res = await list('experiment=1&wave=9')
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ n_selected: 0, rows: [] })
  })

  it('answers a selection with no traits with no rows', async () => {
    expect(await (await list('scan=200')).json()).toEqual({ n_selected: 1, rows: [] })
  })

  it('404 for a deleted experiment', async () => {
    useFake({ experimentVisible: false })
    expect((await list('experiment=1')).status).toBe(404)
  })

  it('502 naming the code and batch, without raw text, when a batch fails', async () => {
    useFake({ failOn: { method: 'listRecipes', nth: 1, error: { code: '57014', message: 'RAW' } } })
    const res = await list('experiment=1')
    expect(res.status).toBe(502)
    const body = await res.json()
    // The whole selection is one listing call (LISTING_BATCH_SCANS, tasks.md 7.4).
    expect(body.detail).toMatch(/57014\) in batch 1 of 1/)
    expect(JSON.stringify(body)).not.toContain('RAW')
  })
})

describe('aborts', () => {
  it("cancels a user's older listing, and the newer one waits for its in-flight call (10b.1)", async () => {
    // One listing call in flight per user: rapid filter changes cannot fill the
    // process-wide slots with calls that are still finishing.
    useFake({ hold: true })
    const tick = () => new Promise((r) => setImmediate(r))
    const older = list('experiment=1')
    while (fake.pending.length === 0) await tick()
    const issued = fake.calls.length
    const newer = list('experiment=1&age=0')
    for (let i = 0; i < 20; i++) await tick()
    expect(fake.calls.length).toBe(issued)
    void fake.drain()
    expect((await older).status).toBe(499)
    expect((await newer).status).toBe(200)
  })

  it('aborts when the request is aborted', async () => {
    useFake({ hold: true })
    const ctrl = new AbortController()
    const p = list('experiment=1', { signal: ctrl.signal })
    await new Promise((r) => setImmediate(r))
    ctrl.abort()
    void fake.drain()
    expect((await p).status).toBe(499)
  })
})

describe('process-wide limit (tasks 6.7, 10a.6a)', () => {
  // Since 7.4 a listing is a few serial calls, so the listing has to arrive while the
  // two jobs hold every slot: its first call must then wait in the semaphore queue.
  const tick = () => new Promise((r) => setImmediate(r))
  const releaseHeld = async () => {
    for (const release of fake.pending.splice(0)) release()
    await tick()
  }

  it('two jobs and one listing never have more than 3 requests in flight, and the listing waits its turn', async () => {
    useFake({ hold: true })
    const post = (sub: string) => {
      signIn(sub)
      return jobsRoute.POST(
        new Request(`http://localhost/x?experiment=1&recipe=${K}&chosen=user`, {
          method: 'POST',
          headers: { 'sec-fetch-site': 'same-origin' },
        })
      )
    }
    const a = post('user-a')
    const b = post('user-b')
    let started = 0
    void a.then(() => started++)
    void b.then(() => started++)
    // Release parked calls until both jobs are accepted and their builds hold all 3 slots.
    for (let i = 0; i < 500; i++) {
      await tick()
      if (started === 2 && fake.inFlight === 3 && fake.pending.length === 3) break
      await releaseHeld()
    }
    expect((await a).status).toBe(202)
    expect((await b).status).toBe(202)
    expect(fake.inFlight).toBe(3)

    signIn('user-c')
    const issuedBefore = fake.calls.length
    const l = list('experiment=1')
    for (let i = 0; i < 20; i++) await tick()
    expect(fake.calls.length).toBe(issuedBefore)

    void fake.drain()
    expect((await l).status).toBe(200)
    for (let i = 0; i < 50; i++) await tick()
    expect(fake.peak).toBe(3)
  })
})
