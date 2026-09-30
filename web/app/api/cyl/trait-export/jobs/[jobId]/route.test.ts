/** GET (status) and DELETE /api/cyl/trait-export/jobs/{jobId} (design D1, D7). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/supabase/server', () => ({
  getSession: vi.fn(),
  createServerSupabaseClient: vi.fn(),
}))

import * as routeModule from '@/app/api/cyl/trait-export/jobs/[jobId]/route'
import { reserveJob } from '@/lib/cyl-trait-export/jobs'
import { resetExportStateForTests } from '@/lib/cyl-trait-export/state'
import { createServerSupabaseClient, getSession } from '@/lib/supabase/server'

const b64url = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url')
const token = (sub: string) =>
  `${b64url({ alg: 'HS256' })}.${b64url({ sub, exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`

let getUser: ReturnType<typeof vi.fn>
function signIn(sub: string) {
  vi.mocked(getSession).mockResolvedValue({ access_token: token(sub) } as never)
  getUser.mockResolvedValue({ data: { user: { id: sub } }, error: null })
}

const call = (method: 'GET' | 'DELETE', jobId: string, headers: Record<string, string> = {}) =>
  routeModule[method](
    new Request(`http://localhost/api/cyl/trait-export/jobs/${jobId}`, {
      method,
      headers: { 'sec-fetch-site': 'same-origin', ...headers },
    }),
    { params: Promise.resolve({ jobId }) }
  )

function startJob(userId: string, run: () => Promise<{ stem: string; chunks: Uint8Array[] }>) {
  const r = reserveJob(userId)
  if (!r.ok) throw new Error('setup')
  return r.start(Date.now() / 1000 + 3600, run)
}

beforeEach(() => {
  getUser = vi.fn()
  vi.mocked(createServerSupabaseClient).mockResolvedValue({ auth: { getUser } } as never)
  signIn('user-1')
})
afterEach(() => {
  vi.clearAllMocks()
  resetExportStateForTests()
})

describe('module contract', () => {
  it('is a dynamic node route that refuses HEAD', async () => {
    expect(routeModule.dynamic).toBe('force-dynamic')
    expect(routeModule.runtime).toBe('nodejs')
    expect((await routeModule.HEAD()).status).toBe(405)
  })
})

describe('guards', () => {
  it.each(['GET', 'DELETE'] as const)('%s: 403 cross-site', async (m) => {
    expect((await call(m, crypto.randomUUID(), { 'sec-fetch-site': 'cross-site' })).status).toBe(
      403
    )
  })

  it.each(['GET', 'DELETE'] as const)('%s: 401 unverified, 503 when GoTrue fails', async (m) => {
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 401, message: 'x' } })
    expect((await call(m, crypto.randomUUID())).status).toBe(401)
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 502, message: 'x' } })
    expect((await call(m, crypto.randomUUID())).status).toBe(503)
  })

  it.each(['GET', 'DELETE'] as const)('%s: 404 for a non-UUID or unknown id', async (m) => {
    expect((await call(m, 'not-a-uuid')).status).toBe(404)
    expect((await call(m, crypto.randomUUID())).status).toBe(404)
  })

  it("404 for another user's job, which a DELETE does not cancel", async () => {
    let resolve!: (v: { stem: string; chunks: Uint8Array[] }) => void
    const jobId = startJob('user-1', () => new Promise((r) => (resolve = r)))
    signIn('user-2')
    expect((await call('GET', jobId)).status).toBe(404)
    expect((await call('DELETE', jobId)).status).toBe(404)
    signIn('user-1')
    expect(await (await call('GET', jobId)).json()).toMatchObject({ status: 'running' })
    resolve({ stem: 's', chunks: [] })
  })
})

describe('status and delete', () => {
  it('returns only the whitelisted status fields', async () => {
    const jobId = startJob('user-1', async () => ({
      stem: 'exp_1bad3d73_20261002',
      chunks: [new Uint8Array(3)],
    }))
    await new Promise((r) => setImmediate(r))
    const res = await call('GET', jobId)
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({
      status: 'ready',
      phase: 'done',
      done: 0,
      total: 0,
      filename: 'exp_1bad3d73_20261002.zip',
    })
  })

  it('cancels a running job with 204', async () => {
    const jobId = startJob('user-1', () => new Promise(() => {}))
    const res = await call('DELETE', jobId)
    expect(res.status).toBe(204)
    expect(await (await call('GET', jobId)).json()).toMatchObject({ status: 'cancelled' })
  })
})
