/**
 * POST /api/cyl/trait-export/jobs (design D7; spec "Trait export routes guard access
 * and limit jobs"). Identity is mocked at the supabase helpers; the database is the
 * golden fake.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fakeDb, type FakeOptions } from '@/lib/cyl-trait-export/__fixtures__/fake-db'

vi.mock('@/lib/supabase/server', () => ({
  getSession: vi.fn(),
  createServerSupabaseClient: vi.fn(),
}))
vi.mock('@/lib/cyl-trait-export/db', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/cyl-trait-export/db')>()),
  createExportDb: vi.fn(),
}))

import * as routeModule from '@/app/api/cyl/trait-export/jobs/route'
import { createExportDb } from '@/lib/cyl-trait-export/db'
import { deleteJob, reserveJob } from '@/lib/cyl-trait-export/jobs'
import { MAX_HELD_BYTES } from '@/lib/cyl-trait-export/limits'
import { getExportState, resetExportStateForTests } from '@/lib/cyl-trait-export/state'
import { createServerSupabaseClient, getSession } from '@/lib/supabase/server'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const b64url = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url')
const token = (sub: string, secondsLeft: number) =>
  `${b64url({ alg: 'HS256' })}.${b64url({
    sub,
    exp: Math.floor(Date.now() / 1000) + secondsLeft,
  })}.sig`

let fake: ReturnType<typeof fakeDb>
let getUser: ReturnType<typeof vi.fn>

function signIn(sub = 'user-1', secondsLeft = 3600) {
  vi.mocked(getSession).mockResolvedValue({ access_token: token(sub, secondsLeft) } as never)
  getUser.mockResolvedValue({ data: { user: { id: sub } }, error: null })
}

function useFake(opts: FakeOptions = {}) {
  fake = fakeDb(opts)
  vi.mocked(createExportDb).mockReturnValue(fake.db)
}

function post(query: string, headers: Record<string, string> = {}) {
  return routeModule.POST(
    new Request(`http://localhost/api/cyl/trait-export/jobs?${query}`, {
      method: 'POST',
      headers: { 'sec-fetch-site': 'same-origin', ...headers },
    })
  )
}

const ok = `experiment=1&recipe=${K}&chosen=user`

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

describe('module contract', () => {
  it('is a dynamic node route', () => {
    expect(routeModule.dynamic).toBe('force-dynamic')
    expect(routeModule.runtime).toBe('nodejs')
  })

  it('refuses HEAD', async () => {
    const res = await routeModule.HEAD()
    expect(res.status).toBe(405)
  })
})

describe('check order', () => {
  it('403 for a cross-site request, before anything else', async () => {
    const res = await post(ok, { 'sec-fetch-site': 'cross-site' })
    expect(res.status).toBe(403)
    expect(getSession).not.toHaveBeenCalled()
    expect(fake.calls).toEqual([])
  })

  // Spec: any Sec-Fetch-Site other than same-origin is 403; a sibling subdomain is same-site (10a.6c).
  it.each(['same-site', 'none'])('403 for Sec-Fetch-Site %s', async (site) => {
    expect((await post(ok, { 'sec-fetch-site': site })).status).toBe(403)
    expect(fake.calls).toEqual([])
  })

  it('401 with no session', async () => {
    vi.mocked(getSession).mockResolvedValue(null as never)
    const res = await post(ok)
    expect(res.status).toBe(401)
  })

  it('401 when GoTrue rejects the token, taking no slot', async () => {
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 401, message: 'bad jwt' } })
    const res = await post(ok)
    expect(res.status).toBe(401)
    expect(getExportState().jobs.size).toBe(0)
    expect(fake.calls).toEqual([])
  })

  it("401 when the token's subject is not the verified user", async () => {
    vi.mocked(getSession).mockResolvedValue({ access_token: token('someone-else', 3600) } as never)
    getUser.mockResolvedValue({ data: { user: { id: 'user-1' } }, error: null })
    expect((await post(ok)).status).toBe(401)
  })

  it('503 when GoTrue errors', async () => {
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 500, message: 'down' } })
    const res = await post(ok)
    expect(res.status).toBe(503)
  })

  it.each([
    [
      'a network failure (auth-js AuthRetryableFetchError, status 0)',
      { name: 'AuthRetryableFetchError', status: 0, message: 'fetch failed' },
    ],
    ['an error with no status', { message: 'unknown' }],
  ])('503 when GoTrue is unreachable: %s (tasks.md 10a.2)', async (_label, error) => {
    getUser.mockResolvedValue({ data: { user: null }, error })
    const res = await post(ok)
    expect(res.status).toBe(503)
    expect(fake.calls).toEqual([])
  })

  it('401 with fewer than 1,800 seconds left, and accepted at 1,800', async () => {
    // The token's exp and the route's floor each read the clock; on the real clock a
    // second boundary between them failed the 1,800 case (10a.6f). Only Date is faked,
    // pinned just before a boundary, so the route's async work still runs.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-02T12:00:00.999Z'))
    try {
      signIn('user-1', 1799)
      const soon = await post(ok)
      expect(soon.status).toBe(401)
      expect(await soon.json()).toEqual({ detail: 'session expires too soon' })
      signIn('user-1', 1800)
      expect((await post(ok)).status).toBe(202)
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([
    'experiment=1&age=-1',
    'experiment=1&age=07',
    'experiment=1&age=1.5',
    'experiment=1&age=%2B7',
    'experiment=1&age=1e3',
    'experiment=1&age=1&age=2',
    'experiment=1234567890123456',
    'experiment=0',
    'scan=5&wave=3',
    'scan=5&age=0',
    '',
    'experiment=1&scan=5',
  ])('422 for %s', async (sel) => {
    const res = await post(`${sel}&recipe=${K}&chosen=user`)
    expect(res.status).toBe(422)
    expect(fake.calls).toEqual([])
  })

  it.each([
    `experiment=1&recipe=abc&chosen=user`,
    `experiment=1&recipe=${K.toUpperCase()}&chosen=user`,
    `experiment=1&recipe=${K}`,
    `experiment=1&recipe=${K}&chosen=maybe`,
  ])('422 for %s', async (q) => {
    expect((await post(q)).status).toBe(422)
  })

  it('429 with the running job id for a user with a running job', async () => {
    const mine = reserveJob('user-1')
    const res = await post(ok)
    expect(res.status).toBe(429)
    expect(await res.json()).toMatchObject({ job_id: mine.ok ? mine.jobId : null })
    expect(fake.calls).toEqual([])
  })

  it('409 when its job was cancelled before the build started (tasks.md 10a.1)', async () => {
    useFake({ hold: true })
    const first = post(ok)
    while (fake.pending.length === 0) await new Promise((r) => setImmediate(r))
    const second = await post(ok)
    expect(second.status).toBe(429)
    const { job_id } = await second.json()
    expect(deleteJob('user-1', job_id)).toBe(true)
    const drained = fake.drain()
    const res = await first
    await drained
    expect(res.status).toBe(409)
    expect(await res.json()).toEqual({ detail: 'the export was cancelled before it started' })
    expect(
      fake.calls.filter((c) => ['listRecipes', 'coverage', 'traits'].includes(c.method))
    ).toEqual([])
  })

  it('429 when two jobs are running', async () => {
    reserveJob('u2')
    reserveJob('u3')
    expect((await post(ok)).status).toBe(429)
    expect(fake.calls).toEqual([])
  })

  it('429 when the memory budget is full', async () => {
    const other = reserveJob('u2')
    if (!other.ok) throw new Error('setup')
    other.start(Date.now() / 1000 + 3600, async () => ({
      stem: 's',
      chunks: [{ byteLength: MAX_HELD_BYTES } as Uint8Array],
    }))
    await new Promise((r) => setImmediate(r))
    expect((await post(ok)).status).toBe(429)
  })

  it.each([
    ['an invisible or deleted experiment', ok, { experimentVisible: false }],
    [
      'a scan whose experiment is deleted',
      `scan=100&recipe=${K}&chosen=user`,
      { experimentVisible: false },
    ],
    ['an unknown scan', `scan=424242&recipe=${K}&chosen=user`, {}],
    ['an empty selection', `experiment=1&wave=9&recipe=${K}&chosen=user`, {}],
  ] as [string, string, FakeOptions][])('404 for %s, releasing the slot', async (_n, q, opts) => {
    useFake(opts)
    const res = await post(q)
    expect(res.status).toBe(404)
    expect(getExportState().jobs.size).toBe(0)
    expect(fake.calls.some((c) => ['listRecipes', 'coverage', 'traits'].includes(c.method))).toBe(
      false
    )
  })

  it('409 when the selection changes while it is read, releasing the slot', async () => {
    useFake({ tamper: { countScans: (n: unknown) => (n as number) + 1 } })
    const res = await post(ok)
    expect(res.status).toBe(409)
    expect(await res.json()).toEqual({ detail: 'the selection changed during the export; retry' })
    expect(getExportState().jobs.size).toBe(0)
  })

  it('502 with a fixed detail when the selection read fails, releasing the slot', async () => {
    useFake({ failOn: { method: 'pageScans', nth: 1, error: { code: 'XX000', message: 'RAW' } } })
    const res = await post(ok)
    expect(res.status).toBe(502)
    expect(JSON.stringify(await res.json())).not.toContain('RAW')
    expect(getExportState().jobs.size).toBe(0)
  })
})

describe('success', () => {
  it('202 with a UUID job id', async () => {
    const res = await post(ok)
    expect(res.status).toBe(202)
    const body = await res.json()
    expect(body.job_id).toMatch(UUID)
  })

  it('reads the selection as the verified user', async () => {
    await post(ok)
    expect(createExportDb).toHaveBeenCalledWith(expect.stringMatching(/\.sig$/))
  })

  it('accepts age=0 and wave=0 and passes them to the selection query', async () => {
    const res = await post(`experiment=1&wave=1&age=0&recipe=${K}&chosen=user`)
    expect(res.status).toBe(202)
    const q = fake.calls.find((c) => c.method === 'pageScans')!.args[0]
    expect(q).toEqual({ experimentId: 1, wave: 1, age: 0 })
    useFake()
    signIn('user-2')
    await post(`experiment=1&wave=0&recipe=${K}&chosen=user`)
    // wave 0 matches nothing in the fixture, so it reaches the query and 404s.
    expect(fake.calls.find((c) => c.method === 'pageScans')!.args[0]).toEqual({
      experimentId: 1,
      wave: 0,
      age: undefined,
    })
  })
})
