/**
 * The in-process export job registry (design D1, D7; tasks.md 5.5). Builds are
 * injected, so no fflate or database work runs under the fake timers.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportError } from './errors'
import {
  deleteJob,
  getJob,
  jobDownload,
  reserveJob,
  sweepExpiredJobs,
  type BuildResult,
  type RunContext,
} from './jobs'
import {
  EXPORT_MAX_SECONDS,
  MAX_HELD_BYTES,
  RETAIN_SECONDS,
  RUNNING_JOB_RESERVE_BYTES,
} from './limits'
import { getExportState, resetExportStateForTests } from './state'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const HOUR = 3600

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const result = (bytes = 10, stem = 'stem'): BuildResult => ({
  stem,
  chunks: [new Uint8Array(bytes)],
})

/** Reserve and start a job whose build the test controls. */
function startControlled(userId: string, tokenExp = Date.now() / 1000 + HOUR) {
  const r = reserveJob(userId)
  if (!r.ok) throw new Error(`refused: ${r.reason}`)
  const gate = deferred<BuildResult>()
  let ctx!: RunContext
  const jobId = r.start(tokenExp, (c) => {
    ctx = c
    return gate.promise
  })
  return { jobId, gate, ctx: () => ctx }
}

const settle = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-02T12:00:00Z'))
})
afterEach(() => {
  const s = getExportState()
  expect(
    [...s.jobs.values()].filter((j) => (j as { status: string }).status === 'running')
  ).toEqual([])
  vi.useRealTimers()
  resetExportStateForTests()
})

describe('lifecycle', () => {
  it('runs to ready with progress, under a random UUID', async () => {
    const { jobId, gate, ctx } = startControlled('u1')
    expect(jobId).toMatch(UUID)
    expect(getJob('u1', jobId)).toEqual({ status: 'running', phase: 'queued', done: 0, total: 0 })
    ctx().onProgress({ phase: 'traits', done: 2, total: 5 })
    expect(getJob('u1', jobId)).toEqual({ status: 'running', phase: 'traits', done: 2, total: 5 })
    gate.resolve(result(10, 'fixture_1bad3d73_20261002'))
    await settle()
    expect(getJob('u1', jobId)).toEqual({
      status: 'ready',
      phase: 'done',
      done: 5,
      total: 5,
      filename: 'fixture_1bad3d73_20261002.zip',
    })
  })

  it('hides a job from anyone but its owner', async () => {
    const { jobId, gate } = startControlled('u1')
    expect(getJob('u2', jobId)).toBeNull()
    expect(jobDownload('u2', jobId)).toBeNull()
    expect(deleteJob('u2', jobId)).toBe(false)
    expect(getJob('u1', jobId)?.status).toBe('running')
    gate.resolve(result())
    await settle()
  })

  it('serves the zip only when ready, and repeatedly within retention', async () => {
    const { jobId, gate } = startControlled('u1')
    expect(jobDownload('u1', jobId)).toEqual({ kind: 'not_ready', status: 'running' })
    gate.resolve(result(10))
    await settle()
    const d1 = jobDownload('u1', jobId)
    const d2 = jobDownload('u1', jobId)
    expect(d1).toMatchObject({ kind: 'ready', filename: 'stem.zip', bytes: 10 })
    expect(d2).toMatchObject({ kind: 'ready' })
  })

  it('whitelists the status fields and drops the build once finished', async () => {
    const { jobId, gate } = startControlled('u1')
    gate.resolve(result())
    await settle()
    expect(Object.keys(getJob('u1', jobId)!).sort()).toEqual(
      ['done', 'filename', 'phase', 'status', 'total'].sort()
    )
    const record = getExportState().jobs.get(jobId) as Record<string, unknown>
    expect(record.run).toBeUndefined()
    expect(record.controller).toBeUndefined()
  })
})

describe('retention', () => {
  it('keeps a finished job for exactly RETAIN_SECONDS', async () => {
    const { jobId, gate } = startControlled('u1')
    gate.resolve(result())
    await settle()
    vi.advanceTimersByTime((RETAIN_SECONDS - 1) * 1000)
    expect(getJob('u1', jobId)?.status).toBe('ready')
    vi.advanceTimersByTime(2000)
    expect(getJob('u1', jobId)).toBeNull()
    expect(jobDownload('u1', jobId)).toBeNull()
  })

  it('sweeps expired jobs out of memory', async () => {
    const { jobId, gate } = startControlled('u1')
    gate.resolve(result())
    await settle()
    vi.setSystemTime(Date.now() + (RETAIN_SECONDS + 1) * 1000)
    sweepExpiredJobs()
    expect(getExportState().jobs.has(jobId)).toBe(false)
  })

  it("drops a user's finished job when their next job is accepted, not when refused", async () => {
    const first = startControlled('u1')
    first.gate.resolve(result())
    await settle()
    const refused = reserveJob('u1')
    expect(refused.ok).toBe(true)
    if (refused.ok) refused.release()
    expect(getJob('u1', first.jobId)?.status).toBe('ready')
    const second = startControlled('u1')
    expect(getJob('u1', first.jobId)).toBeNull()
    second.gate.resolve(result())
    await settle()
  })
})

describe('limits', () => {
  it('allows one running job per user and returns its id', async () => {
    const a = startControlled('u1')
    const r = reserveJob('u1')
    expect(r).toEqual({ ok: false, reason: 'user_limit', runningJobId: a.jobId })
    a.gate.resolve(result())
    await settle()
    const again = reserveJob('u1')
    expect(again.ok).toBe(true)
    if (again.ok) again.release()
  })

  it('allows two running jobs in all', async () => {
    const a = startControlled('u1')
    const b = startControlled('u2')
    expect(reserveJob('u3')).toEqual({ ok: false, reason: 'global_limit' })
    a.gate.reject(new ExportError('rpc', 'x'))
    await settle()
    const c = reserveJob('u3')
    expect(c.ok).toBe(true)
    if (c.ok) c.release()
    b.gate.resolve(result())
    await settle()
  })

  it('counts a reservation as running until it is started or released', () => {
    const r = reserveJob('u1')
    expect(reserveJob('u1')).toMatchObject({ ok: false, reason: 'user_limit' })
    if (r.ok) r.release()
    const again = reserveJob('u1')
    expect(again.ok).toBe(true)
    if (again.ok) again.release()
  })

  it('refuses a start that would exceed the memory budget', async () => {
    const big = Math.floor((MAX_HELD_BYTES - RUNNING_JOB_RESERVE_BYTES) / 2) + 1
    const a = startControlled('u1')
    a.gate.resolve(result(big))
    const b = startControlled('u2')
    b.gate.resolve(result(big))
    await settle()
    expect(reserveJob('u3')).toEqual({ ok: false, reason: 'memory' })
    // The caller's own finished job does not count against it.
    const mine = reserveJob('u1')
    expect(mine.ok).toBe(true)
    if (mine.ok) mine.release()
  })
})

describe('cancel, deadline and failure', () => {
  it('cancels a running job and aborts its work', async () => {
    const { jobId, gate, ctx } = startControlled('u1')
    expect(deleteJob('u1', jobId)).toBe(true)
    expect(ctx().signal.aborted).toBe(true)
    expect(getJob('u1', jobId)?.status).toBe('cancelled')
    gate.reject(new ExportError('cancelled', 'x'))
    await settle()
    expect(getJob('u1', jobId)?.status).toBe('cancelled')
    const next = reserveJob('u1')
    expect(next.ok).toBe(true)
    if (next.ok) next.release()
  })

  it('drops a finished job on DELETE', async () => {
    const { jobId, gate } = startControlled('u1')
    gate.resolve(result())
    await settle()
    expect(deleteJob('u1', jobId)).toBe(true)
    expect(getJob('u1', jobId)).toBeNull()
  })

  it('fails a job that outlives its deadline, even if its build never settles', async () => {
    const { jobId, ctx } = startControlled('u1')
    vi.advanceTimersByTime(EXPORT_MAX_SECONDS * 1000 - 1)
    expect(getJob('u1', jobId)?.status).toBe('running')
    vi.advanceTimersByTime(1)
    expect(getJob('u1', jobId)).toMatchObject({
      status: 'failed',
      detail: 'the export took too long',
    })
    expect(ctx().signal.aborted).toBe(true)
    const next = reserveJob('u1')
    expect(next.ok).toBe(true)
    if (next.ok) next.release()
  })

  it('takes the deadline from the token when it expires sooner', () => {
    const tokenExp = Date.now() / 1000 + 600
    const { jobId } = startControlled('u1', tokenExp)
    vi.advanceTimersByTime((600 - 60) * 1000)
    expect(getJob('u1', jobId)?.status).toBe('failed')
  })

  it('shows an ExportError detail, and a generic one for anything else', async () => {
    const a = startControlled('u1')
    a.gate.reject(new ExportError('rpc', 'a trait read failed (XX000) in batch 1 of 1'))
    await settle()
    expect(getJob('u1', a.jobId)).toMatchObject({
      status: 'failed',
      detail: 'a trait read failed (XX000) in batch 1 of 1',
    })
    const b = startControlled('u2')
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    b.gate.reject(new TypeError('secret internals'))
    await settle()
    expect(getJob('u2', b.jobId)).toMatchObject({ status: 'failed', detail: 'the export failed' })
    expect(JSON.stringify(getJob('u2', b.jobId))).not.toContain('secret')
    err.mockRestore()
  })
})
