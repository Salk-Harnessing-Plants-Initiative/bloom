// @vitest-environment jsdom
/**
 * The Download traits dialog's wiring (tasks.md 11.3; spec "Trait download dialog
 * recipe list" and "Trait download dialog job lifecycle"). The helpers' own cases are
 * in lib/cyl-trait-export/client/.
 */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ refreshSession: vi.fn() }))
vi.mock('@/lib/supabase/client', () => ({ createClientSupabaseClient: () => ({ auth }) }))

import {
  CANCELLED,
  HELP_URL,
  INTERRUPTED,
  POLL_FAILED,
  SIGN_IN,
  TraitExportDialog,
  type ExportTarget,
} from './TraitExportDialog'

const K = '1911b908' + 'a'.repeat(56)
const JOB = '0f8fad5b-d9cb-469f-a165-70867728950e'
const JOB2 = '7c9e6679-7425-40de-944b-e07fc1f90ae7'
const BLOB = { size: 3 } as unknown as Blob

type Res = {
  ok: boolean
  status: number
  headers: Headers
  json: () => Promise<unknown>
  blob: () => Promise<Blob>
}
function reply(
  status: number,
  body: unknown = null,
  opts: { blob?: () => Promise<Blob>; badJson?: boolean } = {}
): Res {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => {
      if (opts.badJson) throw new SyntaxError('not json')
      return body
    },
    blob: opts.blob ?? (async () => BLOB),
  }
}

const LISTING = {
  n_selected: 100,
  rows: [
    {
      recipe_key: K,
      recipe_kind: 'pipeline',
      recipe_key_version: 1,
      definition: {
        models: [['primary-root', 'v2', 'sha256:p']],
        predict_code_sha: 'abc1234',
        traits_code_sha: 'def4567',
      },
      n_scans: 3,
      newest_source_id: 300,
      is_default: true,
    },
    {
      recipe_key: 'legacy:5',
      recipe_kind: 'legacy',
      recipe_key_version: null,
      definition: { source_id: 5, source_name: 'legacy-five' },
      n_scans: 60,
      newest_source_id: 5,
      is_default: false,
    },
  ],
}

type Handler = (url: URL, init: RequestInit | undefined) => Promise<Res> | Res
let routes: {
  listing: Handler
  start: Handler
  status: Handler
  download: Handler
  remove: Handler
}
const fetchSpy = vi.fn()

function kind(url: URL, method: string): keyof typeof routes {
  if (url.pathname.endsWith('/recipes')) return 'listing'
  if (url.pathname.endsWith('/download')) return 'download'
  if (url.pathname.endsWith('/jobs')) return 'start'
  return method === 'DELETE' ? 'remove' : 'status'
}

function calls(k: keyof typeof routes): { url: URL; init?: RequestInit }[] {
  return fetchSpy.mock.calls
    .map(([u, init]) => ({
      url: new URL(String(u), 'http://localhost'),
      init: init as RequestInit | undefined,
    }))
    .filter((c) => kind(c.url, c.init?.method ?? 'GET') === k)
}

const q = (c: { url: URL }) => Object.fromEntries(c.url.searchParams.entries())

/** Status replies in order; the last one repeats. */
function statuses(...rs: (() => Res | Promise<Res>)[]): Handler {
  let i = 0
  return () => rs[Math.min(i++, rs.length - 1)]()
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms))
async function settle() {
  for (let i = 0; i < 10; i++) await tick()
}

const createObjectURL = vi.fn(() => 'blob:export')
const revokeObjectURL = vi.fn()
let anchorClick: ReturnType<typeof vi.spyOn>
let consoleError: ReturnType<typeof vi.spyOn>

const EXPERIMENT: ExportTarget = {
  experimentId: 1,
  wave: 2,
  age: 0,
  waves: [1, 2, 3],
  ages: [0, 7],
}
const onClose = vi.fn()

async function open(target: ExportTarget = EXPERIMENT) {
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<TraitExportDialog target={target} onClose={onClose} />)
  })
  await settle()
  return r
}

const download = () => screen.getByRole('button', { name: /^download$/i }) as HTMLButtonElement
const alertText = () => screen.getByRole('alert').textContent ?? ''
async function click(el: HTMLElement) {
  fireEvent.click(el)
  await settle()
}
async function startDownload() {
  await click(download())
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-02T12:00:00Z'))
  auth.refreshSession.mockReset()
  auth.refreshSession.mockResolvedValue({ data: { session: {} }, error: null })
  routes = {
    listing: () => reply(200, LISTING),
    start: () => reply(202, { job_id: JOB }),
    status: () =>
      reply(200, {
        status: 'ready',
        phase: 'done',
        done: 1,
        total: 1,
        filename: 'exp_1911b908_20261002.zip',
      }),
    download: () => reply(200),
    remove: () => reply(204),
  }
  fetchSpy.mockReset()
  fetchSpy.mockImplementation((u: string, init?: RequestInit) => {
    const url = new URL(String(u), 'http://localhost')
    return Promise.resolve(routes[kind(url, init?.method ?? 'GET')](url, init))
  })
  vi.stubGlobal('fetch', fetchSpy)
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()
  Object.defineProperty(URL, 'createObjectURL', {
    value: createObjectURL,
    configurable: true,
    writable: true,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    value: revokeObjectURL,
    configurable: true,
    writable: true,
  })
  anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  consoleError = vi.spyOn(console, 'error')
  onClose.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete (URL as unknown as Record<string, unknown>).createObjectURL
  delete (URL as unknown as Record<string, unknown>).revokeObjectURL
})

describe('listing', () => {
  it('refreshes the session, then lists at once with the prefilled filters', async () => {
    await open()
    expect(auth.refreshSession).toHaveBeenCalledTimes(1)
    const [list] = calls('listing')
    expect(auth.refreshSession.mock.invocationCallOrder[0]).toBeLessThan(
      fetchSpy.mock.invocationCallOrder[0]
    )
    expect(q(list)).toEqual({ experiment: '1', wave: '2', age: '0' })
    expect((screen.getByLabelText('Wave') as HTMLSelectElement).value).toBe('2')
    expect((screen.getByLabelText('Plant age') as HTMLSelectElement).value).toBe('0')
  })

  it('opens a filter the loaded list lacks as All', async () => {
    await open({ experimentId: 1, wave: 9, age: 7, waves: [1, 2], ages: [7] })
    expect(q(calls('listing')[0])).toEqual({ experiment: '1', age: '7' })
    expect((screen.getByLabelText('Wave') as HTMLSelectElement).value).toBe('all')
  })

  it('shows the sign-in message and lists nothing when the refresh fails', async () => {
    auth.refreshSession.mockResolvedValue({ data: { session: null }, error: new Error('expired') })
    await open()
    expect(calls('listing')).toHaveLength(0)
    expect(alertText()).toContain(SIGN_IN)
  })

  it('treats a thrown refresh like a failed one', async () => {
    auth.refreshSession.mockRejectedValue(new Error('network'))
    await open()
    expect(calls('listing')).toHaveLength(0)
    expect(alertText()).toContain(SIGN_IN)
  })

  it('selects and labels the default, and describes each recipe with its count', async () => {
    await open()
    const def = screen.getByRole('radio', { name: /1911b908/ }) as HTMLInputElement
    expect(def.checked).toBe(true)
    const text = document.body.textContent ?? ''
    expect(text).toContain('default')
    expect(text).toContain('3 of 100 scans')
    expect(text).toContain('60 of 100 scans')
    expect(text).toContain('Models: primary-root v2')
    expect(text).toContain('Source: legacy-five')
  })

  it('names the recipe covering more scans when the default covers fewer', async () => {
    await open()
    expect(screen.getByTestId('fewer-scans-note').textContent).toMatch(/legacy-5.*60/)
  })

  it('re-lists 500 ms after the last filter change, refreshing first, with Download disabled meanwhile', async () => {
    await open()
    expect(download().disabled).toBe(false)
    fireEvent.change(screen.getByLabelText('Wave'), { target: { value: 'all' } })
    await tick(100)
    expect(download().disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Plant age'), { target: { value: '7' } })
    await tick(499)
    expect(calls('listing')).toHaveLength(1)
    await tick(1)
    await settle()
    expect(calls('listing')).toHaveLength(2)
    expect(q(calls('listing')[1])).toEqual({ experiment: '1', age: '7' })
    expect(auth.refreshSession).toHaveBeenCalledTimes(2)
    expect(download().disabled).toBe(false)
  })

  it('ignores a stale listing and a 499 for a superseded one', async () => {
    const first = deferred<Res>()
    let n = 0
    routes.listing = () => (++n === 1 ? first.promise : reply(200, LISTING))
    await open()
    fireEvent.change(screen.getByLabelText('Wave'), { target: { value: '1' } })
    await tick(500)
    await settle()
    await act(async () =>
      first.resolve(reply(499, { detail: 'the listing was replaced or cancelled' }))
    )
    await settle()
    expect(screen.queryByRole('alert')).toBeNull()
    expect((screen.getByRole('radio', { name: /1911b908/ }) as HTMLInputElement).checked).toBe(true)
  })

  it('shows a 499 for its only listing, with Retry', async () => {
    routes.listing = () => reply(499, { detail: 'the listing was replaced or cancelled' })
    await open()
    expect(alertText()).toContain('the listing was replaced or cancelled')
    routes.listing = () => reply(200, LISTING)
    await click(screen.getByRole('button', { name: /retry/i }))
    expect(auth.refreshSession).toHaveBeenCalledTimes(2)
    expect(calls('listing')).toHaveLength(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([401, 403, 404, 422, 502, 503])(
    "shows a listing %i's detail with Retry",
    async (status) => {
      routes.listing = () => reply(status, { detail: `listing refused ${status}` })
      await open()
      expect(alertText()).toContain(`listing refused ${status}`)
      expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
      expect(download().disabled).toBe(true)
    }
  )

  it('says no scans match, and disables Download, for an empty selection', async () => {
    routes.listing = () => reply(200, { n_selected: 0, rows: [] })
    await open()
    expect(document.body.textContent).toContain('No scans match this wave and age')
    expect(download().disabled).toBe(true)
  })

  it('says there are no trait results, and disables Download, when no recipe is listed', async () => {
    routes.listing = () => reply(200, { n_selected: 12, rows: [] })
    await open()
    expect(document.body.textContent).toContain('No trait results for this selection')
    expect(download().disabled).toBe(true)
  })

  it('keeps a picked recipe across a re-list, and falls back to the default when it is gone', async () => {
    await open()
    await click(screen.getByRole('radio', { name: /legacy-5/ }))
    fireEvent.change(screen.getByLabelText('Wave'), { target: { value: '1' } })
    await tick(500)
    await settle()
    expect((screen.getByRole('radio', { name: /legacy-5/ }) as HTMLInputElement).checked).toBe(true)
    routes.listing = () => reply(200, { n_selected: 10, rows: [LISTING.rows[0]] })
    fireEvent.change(screen.getByLabelText('Wave'), { target: { value: '3' } })
    await tick(500)
    await settle()
    expect((screen.getByRole('radio', { name: /1911b908/ }) as HTMLInputElement).checked).toBe(true)
  })
})

describe('starting the job', () => {
  it('refreshes, then posts the selection, recipe and chosen=default for the default', async () => {
    await open()
    routes.status = () => new Promise<Res>(() => {})
    await startDownload()
    const [post] = calls('start')
    expect(post.init?.method).toBe('POST')
    expect(q(post)).toEqual({ experiment: '1', wave: '2', age: '0', recipe: K, chosen: 'default' })
    expect(auth.refreshSession).toHaveBeenCalledTimes(2)
  })

  it('sends chosen=user for another recipe', async () => {
    await open()
    routes.status = () => new Promise<Res>(() => {})
    await click(screen.getByRole('radio', { name: /legacy-5/ }))
    await startDownload()
    expect(q(calls('start')[0])).toMatchObject({ recipe: 'legacy:5', chosen: 'user' })
  })

  it('sends one start for two clicks', async () => {
    await open()
    routes.status = () => new Promise<Res>(() => {})
    fireEvent.click(download())
    fireEvent.click(download())
    await settle()
    expect(calls('start')).toHaveLength(1)
  })

  it('sends no start when the refresh fails', async () => {
    await open()
    auth.refreshSession.mockResolvedValue({ data: { session: null }, error: new Error('expired') })
    await startDownload()
    expect(calls('start')).toHaveLength(0)
    expect(alertText()).toContain(SIGN_IN)
  })

  it('retries once after a session-too-short 401, then shows the detail', async () => {
    routes.start = () => reply(401, { detail: 'session expires too soon' })
    await open()
    await startDownload()
    expect(calls('start')).toHaveLength(2)
    expect(auth.refreshSession).toHaveBeenCalledTimes(3)
    expect(alertText()).toContain('session expires too soon')
  })

  it('does not retry any other 401', async () => {
    routes.start = () => reply(401, { detail: 'Sign in to download traits.' })
    await open()
    await startDownload()
    expect(calls('start')).toHaveLength(1)
    expect(alertText()).toContain('Sign in to download traits.')
  })

  it("offers to resume the user's running job, and resuming polls it to a save", async () => {
    routes.start = () => reply(429, { detail: 'you already have an export running', job_id: JOB2 })
    await open()
    await startDownload()
    expect(document.body.textContent).toContain('you already have an export running')
    await click(screen.getByRole('button', { name: /resume/i }))
    await tick(2000)
    await settle()
    expect(calls('status')[0].url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB2}`)
    expect(anchorClick).toHaveBeenCalledTimes(1)
    expect(calls('start')).toHaveLength(1)
  })

  it("cancels the user's running job from the offer, and is ready to download again", async () => {
    routes.start = () => reply(429, { detail: 'you already have an export running', job_id: JOB2 })
    await open()
    await startDownload()
    await click(screen.getByRole('button', { name: /^cancel export$/i }))
    const [del] = calls('remove')
    expect(del.url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB2}`)
    expect(download().disabled).toBe(false)
  })

  it("shows a busy server's 429 with Retry and no resume", async () => {
    routes.start = () =>
      reply(429, { detail: 'the server is busy with other exports; try again in a few minutes' })
    await open()
    await startDownload()
    expect(alertText()).toContain('the server is busy')
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it.each([
    [403, 'cross-site request refused'],
    [404, 'no scans match this selection'],
    [409, 'the export was cancelled before it started'],
    [409, 'the selection changed during the export; retry'],
    [422, 'recipe must be a recipe key'],
    [502, 'the selection could not be read'],
  ])('shows a %i start refusal (%s) with Retry and no polling', async (status, detail) => {
    routes.start = () => reply(status, { detail })
    await open()
    await startDownload()
    await tick(10_000)
    expect(alertText()).toContain(detail)
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
    expect(calls('status')).toHaveLength(0)
  })
})

describe('polling', () => {
  it('never sends a second status request while one is held', async () => {
    const held = deferred<Res>()
    routes.status = () => held.promise
    await open()
    await startDownload()
    await tick(2000)
    await tick(20_000)
    expect(calls('status')).toHaveLength(1)
  })

  it('polls every 2 s for the first minute, then every 5 s, showing the batch being read', async () => {
    routes.status = () => reply(200, { status: 'running', phase: 'traits', done: 3, total: 10 })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(screen.getByRole('status').textContent).toContain('Reading batch 3 of 10')
    await tick(58_000)
    const at60 = calls('status').length
    expect(at60).toBe(30)
    await tick(5000)
    expect(calls('status').length).toBe(at60 + 1)
  })

  it('shows a cancelled job with Retry, saves nothing and sends no DELETE on close', async () => {
    routes.status = () => reply(200, { status: 'cancelled', phase: 'traits', done: 1, total: 4 })
    const r = await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(alertText()).toContain(CANCELLED)
    expect(createObjectURL).not.toHaveBeenCalled()
    r.unmount()
    await settle()
    expect(calls('remove')).toHaveLength(0)
  })

  it('after three failed polls offers Check again, which polls the same job without a new start', async () => {
    routes.status = statuses(
      () => reply(503, { detail: 'sign-in service unavailable; try again shortly' }),
      () => Promise.reject(new TypeError('network')),
      () => reply(503, { detail: 'sign-in service unavailable; try again shortly' }),
      () => reply(200, { status: 'running', phase: 'traits', done: 1, total: 4 })
    )
    await open()
    await startDownload()
    for (let i = 0; i < 3; i++) {
      await tick(2000)
      await settle()
    }
    expect(alertText()).toContain(POLL_FAILED)
    await tick(20_000)
    expect(calls('status')).toHaveLength(3)
    await click(screen.getByRole('button', { name: /check again/i }))
    expect(calls('status')).toHaveLength(4)
    expect(calls('status')[3].url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB}`)
    expect(calls('start')).toHaveLength(1)
  })

  it('says the export was interrupted when polling gets a 404, with Retry', async () => {
    routes.status = () =>
      reply(404, { detail: 'export not found; it may have expired or the server restarted' })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(alertText()).toContain(INTERRUPTED)
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it("shows a failed job's detail with Retry and saves nothing; Retry refreshes and starts a new job", async () => {
    const detail = 'a trait read timed out (57014) in batch 12 of 185; try a wave or age filter'
    routes.status = () =>
      reply(200, { status: 'failed', phase: 'traits', done: 12, total: 185, detail })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(alertText()).toContain(detail)
    expect(createObjectURL).not.toHaveBeenCalled()
    const refreshes = auth.refreshSession.mock.calls.length
    await click(screen.getByRole('button', { name: /retry/i }))
    expect(calls('start')).toHaveLength(2)
    expect(auth.refreshSession.mock.calls.length).toBe(refreshes + 1)
  })
})

describe('saving', () => {
  it("downloads, saves under the job's filename, revokes the URL, then deletes the job", async () => {
    let saved = ''
    anchorClick.mockImplementation(function (this: HTMLAnchorElement) {
      saved = this.download
    })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(calls('download')[0].url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB}/download`)
    expect(createObjectURL).toHaveBeenCalledWith(BLOB)
    expect(saved).toBe('exp_1911b908_20261002.zip')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:export')
    const [del] = calls('remove')
    expect(del.url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB}`)
    expect(fetchSpy.mock.invocationCallOrder.at(-1)).toBeGreaterThan(
      anchorClick.mock.invocationCallOrder[0]
    )
  })

  it.each([
    [404, 'export not found; it may have expired or the server restarted'],
    [409, 'the export is running'],
  ])("shows a refused download's detail (%i) and saves nothing", async (status, detail) => {
    const blob = vi.fn(async () => BLOB)
    routes.download = () => reply(status, { detail }, { blob })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(alertText()).toContain(detail)
    expect(blob).not.toHaveBeenCalled()
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('shows the generic message for a rejected blob() or a non-JSON error', async () => {
    routes.download = () =>
      reply(200, null, { blob: async () => Promise.reject(new Error('aborted')) })
    await open()
    await startDownload()
    await tick(2000)
    await settle()
    expect(alertText()).toMatch(/could not be completed/i)
    expect(createObjectURL).not.toHaveBeenCalled()
    cleanup()
    routes.download = () => reply(200)
    routes.start = () => reply(502, null, { badJson: true })
    await open()
    await startDownload()
    expect(alertText()).toMatch(/could not be completed/i)
  })
})

describe('closing', () => {
  it('Close sends one DELETE, calls onClose and stops polling', async () => {
    routes.status = () => reply(200, { status: 'running', phase: 'queued', done: 0, total: 0 })
    const r = await open()
    await startDownload()
    await tick(2000)
    await settle()
    await click(screen.getByRole('button', { name: /^close$/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
    r.unmount()
    await settle()
    const polls = calls('status').length
    await tick(30_000)
    expect(calls('remove')).toHaveLength(1)
    expect(calls('status')).toHaveLength(polls)
  })

  it('Escape closes like Close', async () => {
    await open()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('an unmount while running sends one DELETE', async () => {
    routes.status = () => reply(200, { status: 'running', phase: 'traits', done: 1, total: 4 })
    const r = await open()
    await startDownload()
    r.unmount()
    await settle()
    expect(calls('remove')).toHaveLength(1)
    expect(calls('remove')[0].url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB}`)
  })

  it('sends no start when closed during the session refresh', async () => {
    const r = await open()
    const refresh = deferred<{ data: unknown; error: null }>()
    auth.refreshSession.mockReturnValueOnce(refresh.promise)
    fireEvent.click(download())
    r.unmount()
    await act(async () => refresh.resolve({ data: {}, error: null }))
    await settle()
    expect(calls('start')).toHaveLength(0)
  })

  it('deletes a job whose start answers after the close, and never polls it', async () => {
    const started = deferred<Res>()
    routes.start = () => started.promise
    const r = await open()
    fireEvent.click(download())
    await settle()
    r.unmount()
    await act(async () => started.resolve(reply(202, { job_id: JOB })))
    await settle()
    await tick(10_000)
    expect(calls('remove')).toHaveLength(1)
    expect(calls('remove')[0].url.pathname).toBe(`/api/cyl/trait-export/jobs/${JOB}`)
    expect(calls('status')).toHaveLength(0)
  })

  it('aborts a listing still in flight', async () => {
    routes.listing = () => new Promise<Res>(() => {})
    const r = await open()
    const signal = calls('listing')[0].init?.signal
    expect(signal?.aborted).toBe(false)
    r.unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('sends no DELETE for the job on the resume offer', async () => {
    routes.start = () => reply(429, { detail: 'you already have an export running', job_id: JOB2 })
    const r = await open()
    await startDownload()
    r.unmount()
    await settle()
    expect(calls('remove')).toHaveLength(0)
  })

  it('does nothing for 120 s after close', async () => {
    routes.status = () => reply(200, { status: 'running', phase: 'traits', done: 1, total: 4 })
    const r = await open()
    await startDownload()
    r.unmount()
    await settle()
    const n = fetchSpy.mock.calls.length
    await tick(120_000)
    expect(fetchSpy.mock.calls.length).toBe(n)
    expect(consoleError).not.toHaveBeenCalled()
  })
})

describe('scan grain and help', () => {
  it('lists one scan with no wave or age filter', async () => {
    await open({ scanId: 577 })
    expect(q(calls('listing')[0])).toEqual({ scan: '577' })
    expect(screen.queryByLabelText('Wave')).toBeNull()
    expect(screen.queryByLabelText('Plant age')).toBeNull()
  })

  it('links to the guide on main in a new tab', async () => {
    await open()
    const link = screen.getByRole('link', { name: /what's in this file\?/i })
    expect(link.getAttribute('href')).toBe(HELP_URL)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(HELP_URL).toBe(
      'https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/_WIKI/SUPABASE/trait-recipes.md#using-a-trait-export'
    )
  })
})
