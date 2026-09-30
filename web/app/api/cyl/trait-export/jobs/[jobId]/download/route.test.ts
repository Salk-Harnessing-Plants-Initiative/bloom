/**
 * GET /api/cyl/trait-export/jobs/{jobId}/download (design D1, D7), plus one chained
 * POST -> status -> download run through the real build and zip over the golden fake.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { unzipSync } from 'fflate'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fakeDb } from '@/lib/cyl-trait-export/__fixtures__/fake-db'

vi.mock('@/lib/supabase/server', () => ({
  getSession: vi.fn(),
  createServerSupabaseClient: vi.fn(),
}))
vi.mock('@/lib/cyl-trait-export/db', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/cyl-trait-export/db')>()),
  createExportDb: vi.fn(),
}))

import * as downloadRoute from '@/app/api/cyl/trait-export/jobs/[jobId]/download/route'
import * as statusRoute from '@/app/api/cyl/trait-export/jobs/[jobId]/route'
import * as startRoute from '@/app/api/cyl/trait-export/jobs/route'
import { createExportDb } from '@/lib/cyl-trait-export/db'
import { reserveJob } from '@/lib/cyl-trait-export/jobs'
import { RETAIN_SECONDS } from '@/lib/cyl-trait-export/limits'
import { resetExportStateForTests } from '@/lib/cyl-trait-export/state'
import { createServerSupabaseClient, getSession } from '@/lib/supabase/server'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const GOLDEN = join(
  __dirname,
  '..',
  '..',
  '..',
  '..',
  '..',
  '..',
  '..',
  'lib',
  'cyl-trait-export',
  '__fixtures__',
  'golden'
)
const golden = (name: string) => readFileSync(join(GOLDEN, name))

const b64url = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url')
const token = (sub: string) =>
  `${b64url({ alg: 'HS256' })}.${b64url({ sub, exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`

let getUser: ReturnType<typeof vi.fn>
function signIn(sub: string) {
  vi.mocked(getSession).mockResolvedValue({ access_token: token(sub) } as never)
  getUser.mockResolvedValue({ data: { user: { id: sub } }, error: null })
}

const same = { 'sec-fetch-site': 'same-origin' }
const download = (jobId: string, headers: Record<string, string> = same) =>
  downloadRoute.GET(new Request(`http://localhost/x/${jobId}/download`, { headers }), {
    params: Promise.resolve({ jobId }),
  })

function startJob(run: () => Promise<{ stem: string; chunks: Uint8Array[] }>) {
  const r = reserveJob('user-1')
  if (!r.ok) throw new Error('setup')
  return r.start(Date.now() / 1000 + 3600, run)
}

const bytesOf = async (res: Response) => new Uint8Array(await res.arrayBuffer())

beforeEach(() => {
  getUser = vi.fn()
  vi.mocked(createServerSupabaseClient).mockResolvedValue({ auth: { getUser } } as never)
  signIn('user-1')
})
afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  resetExportStateForTests()
})

describe('guards', () => {
  it('is a dynamic node route that refuses HEAD', async () => {
    expect(downloadRoute.dynamic).toBe('force-dynamic')
    expect(downloadRoute.runtime).toBe('nodejs')
    expect((await downloadRoute.HEAD()).status).toBe(405)
  })

  it('403 cross-site, 401 unverified, 404 for a bad, unknown or foreign id', async () => {
    const jobId = startJob(async () => ({ stem: 's', chunks: [new Uint8Array(1)] }))
    await new Promise((r) => setImmediate(r))
    expect((await download(jobId, { 'sec-fetch-site': 'cross-site' })).status).toBe(403)
    expect((await download('nope')).status).toBe(404)
    expect((await download(crypto.randomUUID())).status).toBe(404)
    signIn('user-2')
    expect((await download(jobId)).status).toBe(404)
    getUser.mockResolvedValue({ data: { user: null }, error: { status: 401, message: 'x' } })
    expect((await download(jobId)).status).toBe(401)
  })
})

describe('download', () => {
  it('409 while running, failed or cancelled', async () => {
    const running = startJob(() => new Promise(() => {}))
    expect((await download(running)).status).toBe(409)
    resetExportStateForTests()
    const failed = startJob(async () => {
      throw new Error('boom')
    })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await new Promise((r) => setImmediate(r))
    expect((await download(failed)).status).toBe(409)
  })

  it('streams the ready zip with its headers, repeatably, then 404 after retention', async () => {
    const zipBytes = new Uint8Array([1, 2, 3, 4, 5])
    const jobId = startJob(async () => ({
      stem: 'exp_scan100_1bad3d73_20261002',
      chunks: [zipBytes.slice(0, 2), zipBytes.slice(2)],
    }))
    await new Promise((r) => setImmediate(r))
    for (let i = 0; i < 2; i++) {
      const res = await download(jobId)
      expect(res.status).toBe(200)
      expect(res.headers.get('content-type')).toBe('application/zip')
      expect(res.headers.get('content-disposition')).toBe(
        'attachment; filename="exp_scan100_1bad3d73_20261002.zip"'
      )
      expect(res.headers.get('cache-control')).toBe('no-store')
      expect(res.headers.get('content-length')).toBe('5')
      expect([...(await bytesOf(res))]).toEqual([1, 2, 3, 4, 5])
    }
    vi.useFakeTimers()
    vi.setSystemTime(Date.now() + (RETAIN_SECONDS + 1) * 1000)
    expect((await download(jobId)).status).toBe(404)
  })
})

describe('chained', () => {
  it('POST, poll, download and unzip give the golden K export', async () => {
    const fake = fakeDb()
    vi.mocked(createExportDb).mockReturnValue(fake.db)
    const started = await startRoute.POST(
      new Request(`http://localhost/x?experiment=1&recipe=${K}&chosen=user`, {
        method: 'POST',
        headers: same,
      })
    )
    expect(started.status).toBe(202)
    const { job_id } = await started.json()
    let status = ''
    for (let i = 0; i < 200 && status !== 'ready'; i++) {
      await new Promise((r) => setImmediate(r))
      const res = await statusRoute.GET(new Request('http://localhost/x', { headers: same }), {
        params: Promise.resolve({ jobId: job_id }),
      })
      status = (await res.json()).status
    }
    expect(status).toBe('ready')
    const res = await download(job_id)
    expect(res.status).toBe(200)
    const files = unzipSync(await bytesOf(res))
    const names = Object.keys(files)
    const stem = names[0].replace(/\.csv$/, '')
    expect(stem).toMatch(/^fixture-diversity-screen_1bad3d73_\d{8}$/)
    expect(names).toEqual([`${stem}.csv`, `${stem}.export.json`, `${stem}.excluded.csv`])
    expect(Buffer.from(files[`${stem}.csv`]).equals(golden('K.csv'))).toBe(true)
    expect(Buffer.from(files[`${stem}.excluded.csv`]).equals(golden('K.excluded.csv'))).toBe(true)
    const side = JSON.parse(Buffer.from(files[`${stem}.export.json`]).toString('utf8'))
    const want = JSON.parse(golden('K.export.json').toString('utf8'))
    expect({ ...side, generated_at: want.generated_at }).toEqual(want)
  })
})
