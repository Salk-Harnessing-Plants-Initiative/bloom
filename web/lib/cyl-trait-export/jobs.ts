/**
 * In-process export jobs (design D1, D7). A job is reserved first (the slot counts
 * from then on), then either started or released. It runs in the background, is
 * visible only to its owner, is kept RETAIN_SECONDS after it finishes, and fails at
 * its deadline even if its build never settles. Jobs live in the process-wide state,
 * so a restart loses them.
 */

import { randomUUID } from 'node:crypto'

import type { Progress } from './build-export'
import { ExportError } from './errors'
import {
  EXPORT_MAX_SECONDS,
  MAX_HELD_BYTES,
  MAX_JOBS_PER_USER,
  MAX_RUNNING_JOBS,
  RETAIN_SECONDS,
  RUNNING_JOB_RESERVE_BYTES,
} from './limits'
import { getExportState } from './state'

export type JobStatus = 'running' | 'ready' | 'failed' | 'cancelled'

/** What the status route may show: nothing else ever leaves the registry. */
export type JobView = {
  status: JobStatus
  phase: string
  done: number
  total: number
  detail?: string
  filename?: string
}

export type BuildResult = { stem: string; chunks: Uint8Array[] }
export type RunContext = { signal: AbortSignal; onProgress: (p: Progress) => void }
type Run = (ctx: RunContext) => Promise<BuildResult>

export type Reservation =
  | {
      ok: true
      jobId: string
      /** Give the slot back (the job never started, e.g. its selection was refused). */
      release: () => void
      /**
       * Start the build in the background; `tokenExp` is the verified token's exp (s).
       * Null if the job was cancelled while its selection was being resolved.
       */
      start: (tokenExp: number, run: Run) => string | null
    }
  | { ok: false; reason: 'user_limit'; runningJobId: string }
  | { ok: false; reason: 'global_limit' | 'memory' }

type JobRecord = {
  id: string
  userId: string
  status: JobStatus
  phase: string
  done: number
  total: number
  detail?: string
  filename?: string
  chunks?: Uint8Array[]
  bytes: number
  started: boolean
  finishedAt?: number
  controller?: AbortController
  run?: Run
  deadline?: ReturnType<typeof setTimeout>
}

const SWEEP_MS = 60_000
const GENERIC_FAILURE = 'the export failed'

function jobs(): Map<string, JobRecord> {
  return getExportState().jobs as Map<string, JobRecord>
}

function expired(rec: JobRecord, now: number): boolean {
  return rec.finishedAt !== undefined && now - rec.finishedAt > RETAIN_SECONDS * 1000
}

export function sweepExpiredJobs(): void {
  const now = Date.now()
  for (const [id, rec] of jobs()) if (expired(rec, now)) jobs().delete(id)
}

function ensureSweeper(): void {
  const state = getExportState()
  if (state.sweeper) return
  state.sweeper = setInterval(sweepExpiredJobs, SWEEP_MS)
  state.sweeper.unref?.()
}

/** The live record for this owner, or null (unknown, foreign or expired). */
function lookup(userId: string, jobId: string): JobRecord | null {
  const rec = jobs().get(jobId)
  if (!rec || rec.userId !== userId) return null
  if (expired(rec, Date.now())) {
    jobs().delete(jobId)
    return null
  }
  return rec
}

function finish(rec: JobRecord): void {
  if (rec.deadline) clearTimeout(rec.deadline)
  rec.deadline = undefined
  rec.finishedAt ??= Date.now()
  rec.run = undefined
  rec.controller = undefined
}

export function reserveJob(userId: string): Reservation {
  const now = Date.now()
  const all = [...jobs().values()].filter((j) => !expired(j, now))
  const running = all.filter((j) => j.status === 'running')
  const mine = running.filter((j) => j.userId === userId)
  if (mine.length >= MAX_JOBS_PER_USER) {
    return { ok: false, reason: 'user_limit', runningJobId: mine[0].id }
  }
  if (running.length >= MAX_RUNNING_JOBS) return { ok: false, reason: 'global_limit' }
  const counted = all.filter((j) => j.status === 'ready' && j.userId !== userId)
  const countedIds = new Set(counted.map((j) => j.id))
  // A zip still being downloaded is held by its stream even after its job is dropped
  // (deleted, expired, or replaced by the user's next job); count it until it closes.
  let streaming = 0
  for (const d of getExportState().downloads.values()) {
    if (!countedIds.has(d.jobId)) streaming += d.bytes
  }
  const held =
    counted.reduce((sum, j) => sum + j.bytes, 0) +
    streaming +
    running.length * RUNNING_JOB_RESERVE_BYTES
  if (held + RUNNING_JOB_RESERVE_BYTES > MAX_HELD_BYTES) return { ok: false, reason: 'memory' }

  const rec: JobRecord = {
    id: randomUUID(),
    userId,
    status: 'running',
    phase: 'queued',
    done: 0,
    total: 0,
    bytes: 0,
    started: false,
  }
  jobs().set(rec.id, rec)
  ensureSweeper()

  return {
    ok: true,
    jobId: rec.id,
    release: () => {
      if (!rec.started) jobs().delete(rec.id)
    },
    start: (tokenExp, run) => {
      // Cancelled (DELETE) between reserve and start: never build.
      if (rec.status !== 'running') return null
      // One finished job per user: accepting a new one drops the previous.
      for (const [id, other] of jobs()) {
        if (id !== rec.id && other.userId === userId && other.status !== 'running') {
          jobs().delete(id)
        }
      }
      rec.started = true
      rec.run = run
      rec.controller = new AbortController()
      const tokenMs = (tokenExp - Date.now() / 1000 - 60) * 1000
      const deadlineMs = Math.max(0, Math.min(EXPORT_MAX_SECONDS * 1000, tokenMs))
      rec.deadline = setTimeout(() => onDeadline(rec), deadlineMs)
      void execute(rec, rec.controller.signal)
      return rec.id
    },
  }
}

function onDeadline(rec: JobRecord): void {
  if (rec.status !== 'running') return
  rec.status = 'failed'
  rec.detail = 'the export took too long'
  rec.controller?.abort()
  finish(rec)
}

async function execute(rec: JobRecord, signal: AbortSignal): Promise<void> {
  const run = rec.run!
  try {
    const result = await run({
      signal,
      onProgress: (p) => {
        if (rec.status !== 'running') return
        rec.phase = p.phase
        rec.done = p.done
        rec.total = p.total
      },
    })
    if (rec.status !== 'running') return
    rec.chunks = result.chunks
    rec.bytes = result.chunks.reduce((sum, c) => sum + c.byteLength, 0)
    rec.filename = `${result.stem}.zip`
    rec.status = 'ready'
    rec.phase = 'done'
    rec.done = rec.total
  } catch (e) {
    if (rec.status !== 'running') return
    if (e instanceof ExportError) {
      rec.status = e.kind === 'cancelled' ? 'cancelled' : 'failed'
      if (rec.status === 'failed') rec.detail = e.detail
    } else {
      console.error('[trait-export] job failed unexpectedly', e)
      rec.status = 'failed'
      rec.detail = GENERIC_FAILURE
    }
  } finally {
    finish(rec)
  }
}

export function getJob(userId: string, jobId: string): JobView | null {
  const rec = lookup(userId, jobId)
  if (!rec) return null
  const view: JobView = { status: rec.status, phase: rec.phase, done: rec.done, total: rec.total }
  if (rec.detail !== undefined) view.detail = rec.detail
  if (rec.filename !== undefined) view.filename = rec.filename
  return view
}

export function jobDownload(
  userId: string,
  jobId: string
):
  | { kind: 'ready'; filename: string; chunks: Uint8Array[]; bytes: number }
  | { kind: 'not_ready'; status: JobStatus }
  | null {
  const rec = lookup(userId, jobId)
  if (!rec) return null
  if (rec.status !== 'ready' || !rec.chunks || !rec.filename) {
    return { kind: 'not_ready', status: rec.status }
  }
  return { kind: 'ready', filename: rec.filename, chunks: rec.chunks, bytes: rec.bytes }
}

let nextDownloadId = 0

/**
 * `jobDownload` for a stream that is about to be served: the zip's bytes are counted
 * against MAX_HELD_BYTES until `close` (idempotent) is called, even if the job is
 * dropped meanwhile (tasks.md 10b.2).
 */
export function openDownload(
  userId: string,
  jobId: string
):
  | { kind: 'ready'; filename: string; chunks: Uint8Array[]; bytes: number; close: () => void }
  | { kind: 'not_ready'; status: JobStatus }
  | null {
  const found = jobDownload(userId, jobId)
  if (!found || found.kind !== 'ready') return found
  const { downloads } = getExportState()
  const id = nextDownloadId++
  downloads.set(id, { jobId, bytes: found.bytes })
  return { ...found, close: () => void downloads.delete(id) }
}

/** Cancel a running job, or drop a finished one. False if not the owner's. */
export function deleteJob(userId: string, jobId: string): boolean {
  const rec = lookup(userId, jobId)
  if (!rec) return false
  if (rec.status === 'running') {
    rec.status = 'cancelled'
    rec.controller?.abort()
    finish(rec)
  } else {
    jobs().delete(jobId)
  }
  return true
}
