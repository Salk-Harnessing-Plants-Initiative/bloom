'use client'

/**
 * The Download traits dialog (spec "Trait download dialog recipe list" and "Trait
 * download dialog job lifecycle"; design D8).
 *
 * - It lists the selection's recipes, the default preselected, and re-lists 500 ms
 *   after a filter change. Only the newest listing it sent counts: the route answers
 *   `499` to a listing the same user replaced, so a `499` is dropped only when this
 *   dialog has sent a newer one.
 * - Download refreshes the session, starts one job, and polls it with chained
 *   timeouts, so polls never overlap. A ready zip is saved under the job's filename
 *   and the job is then deleted, so it stops holding server memory.
 * - The dialog is mounted only while open. Unmounting stops polling, aborts a listing,
 *   and deletes any job it started or resumed, including one whose start answers
 *   after the close, so no orphan job holds the user's one export slot.
 */

import Dialog from '@mui/material/Dialog'
import { useEffect, useId, useRef, useState } from 'react'
import {
  createFailureCounter,
  createLatestGuard,
  phaseLine,
  pollDelay,
} from '@/lib/cyl-trait-export/client/poll'
import {
  describeRecipe,
  emptyState,
  fewerScansNote,
  prefill,
  recipeLabel,
  type FilterValue,
} from '@/lib/cyl-trait-export/client/recipe-view'
import {
  GENERIC_ERROR,
  classifyStartError,
  downloadUrl,
  jobStatusUrl,
  jobUrl,
  readErrorBody,
  recipesUrl,
  type DialogSelection,
} from '@/lib/cyl-trait-export/client/requests'
import type { JobView } from '@/lib/cyl-trait-export/jobs'
import type { RecipeRow } from '@/lib/cyl-trait-export/recipes'
import { createClientSupabaseClient } from '@/lib/supabase/client'

/** What the dialog exports: an experiment (with the page's wave/age and loaded lists) or one scan. */
export type ExportTarget =
  | { experimentId: number; wave?: number; age?: number; waves: number[]; ages: number[] }
  | { scanId: number }

export const HELP_URL =
  'https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/_WIKI/SUPABASE/trait-recipes.md#using-a-trait-export'
export const SIGN_IN = 'Your session has ended. Sign in again to download traits.'
export const INTERRUPTED = 'The export was interrupted (the server restarted); please retry.'
export const CANCELLED = 'The export was cancelled.'
export const POLL_FAILED = "Could not check the export's progress."

const DEBOUNCE_MS = 500
const FALLBACK_FILENAME = 'traits.zip'

type Listing =
  | { state: 'loading' }
  | { state: 'ready'; nSelected: number; rows: RecipeRow[] }
  | { state: 'error'; detail: string }

type Job =
  | { state: 'idle' }
  | { state: 'starting' }
  | { state: 'running'; line: string }
  | { state: 'offer'; jobId: string; detail: string }
  | { state: 'paused' }
  | { state: 'saved'; filename: string }
  | { state: 'error'; detail: string }

async function refreshSession(): Promise<boolean> {
  try {
    const { error } = await createClientSupabaseClient().auth.refreshSession()
    return !error
  } catch {
    return false
  }
}

function removeJob(jobId: string) {
  fetch(jobStatusUrl(jobId), { method: 'DELETE', keepalive: true }).catch(() => {})
}

function selectionOf(target: ExportTarget, wave: FilterValue, age: FilterValue): DialogSelection {
  return 'scanId' in target
    ? { scan: target.scanId }
    : { experiment: target.experimentId, wave, age }
}

export function TraitExportDialog({
  target,
  onClose,
}: {
  target: ExportTarget
  onClose: () => void
}) {
  const headingId = useId()
  const waveId = useId()
  const ageId = useId()
  const recipeName = useId()
  const isExperiment = 'experimentId' in target

  const [wave, setWave] = useState<FilterValue>(() =>
    isExperiment ? prefill(target.wave, target.waves) : 'all'
  )
  const [age, setAge] = useState<FilterValue>(() =>
    isExperiment ? prefill(target.age, target.ages) : 'all'
  )
  const [listing, setListing] = useState<Listing>({ state: 'loading' })
  const [picked, setPicked] = useState<string | null>(null)
  const [job, setJob] = useState<Job>({ state: 'idle' })
  const [relist, setRelist] = useState(0)

  const alive = useRef(true)
  const firstListing = useRef(true)
  const guard = useRef(createLatestGuard())
  const listAbort = useRef<AbortController | null>(null)
  /** The job this dialog started or resumed and has not deleted; deleted on close. */
  const held = useRef<string | null>(null)
  const busy = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const startedAt = useRef(0)
  const failures = useRef(createFailureCounter())

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      clearTimeout(timer.current)
      listAbort.current?.abort()
      if (held.current !== null) removeJob(held.current)
      held.current = null
    }
  }, [])

  useEffect(() => {
    const sel = selectionOf(target, wave, age)
    setListing({ state: 'loading' })

    async function list() {
      const n = guard.current.next()
      listAbort.current?.abort()
      const ctrl = new AbortController()
      listAbort.current = ctrl
      const current = () => alive.current && guard.current.isCurrent(n)
      const fail = (detail: string) => {
        if (current()) setListing({ state: 'error', detail })
      }
      if (!(await refreshSession())) return fail(SIGN_IN)
      if (!current()) return
      let res: Response
      try {
        res = await fetch(recipesUrl(sel), { signal: ctrl.signal, cache: 'no-store' })
      } catch {
        return fail(GENERIC_ERROR)
      }
      if (!current()) return
      if (!res.ok) return fail((await readErrorBody(res)).detail)
      let body: { n_selected: number; rows: RecipeRow[] }
      try {
        body = await res.json()
      } catch {
        return fail(GENERIC_ERROR)
      }
      if (!current()) return
      const rows = Array.isArray(body.rows) ? body.rows : []
      setListing({ state: 'ready', nSelected: body.n_selected, rows })
      setPicked((prev) =>
        prev !== null && rows.some((r) => r.recipe_key === prev)
          ? prev
          : rows.find((r) => r.is_default)?.recipe_key ?? null
      )
    }

    if (firstListing.current) {
      firstListing.current = false
      void list()
      return
    }
    const t = setTimeout(() => void list(), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [target, wave, age, relist])

  const rows = listing.state === 'ready' ? listing.rows : []
  const nSelected = listing.state === 'ready' ? listing.nSelected : 0
  const empty = listing.state === 'ready' ? emptyState(listing.nSelected, listing.rows) : null
  const defaultKey = rows.find((r) => r.is_default)?.recipe_key ?? null
  const note = fewerScansNote(rows)
  const jobActive = ['starting', 'running', 'offer', 'paused'].includes(job.state)
  const canDownload = listing.state === 'ready' && empty === null && picked !== null && !jobActive

  function retryListing() {
    firstListing.current = true
    setRelist((r) => r + 1)
  }

  function schedule() {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void poll(), pollDelay(Date.now() - startedAt.current))
  }

  function follow(jobId: string) {
    held.current = jobId
    startedAt.current = Date.now()
    failures.current = createFailureCounter()
    setJob({ state: 'running', line: phaseLine({ phase: 'queued', done: 0, total: 0 }) })
    schedule()
  }

  function finish(next: Job) {
    busy.current = false
    setJob(next)
  }

  async function poll() {
    const jobId = held.current
    if (jobId === null) return
    let res: Response | null
    try {
      res = await fetch(jobStatusUrl(jobId), { cache: 'no-store' })
    } catch {
      res = null
    }
    if (!alive.current) return
    if (res?.status === 404) {
      held.current = null
      return finish({ state: 'error', detail: INTERRUPTED })
    }
    let view: JobView | null = null
    if (res?.ok) {
      try {
        view = (await res.json()) as JobView
      } catch {
        view = null
      }
    }
    if (!alive.current) return
    if (view === null) {
      if (failures.current.fail()) setJob({ state: 'paused' })
      else schedule()
      return
    }
    failures.current.ok()
    if (view.status === 'running') {
      setJob({ state: 'running', line: phaseLine(view) })
      schedule()
    } else if (view.status === 'ready') {
      await save(jobId, view.filename ?? FALLBACK_FILENAME)
    } else if (view.status === 'cancelled') {
      held.current = null
      finish({ state: 'error', detail: CANCELLED })
    } else {
      finish({ state: 'error', detail: view.detail ?? GENERIC_ERROR })
    }
  }

  async function save(jobId: string, filename: string) {
    setJob({ state: 'running', line: phaseLine({ phase: 'done', done: 0, total: 0 }) })
    let res: Response | null
    try {
      res = await fetch(downloadUrl(jobId), { cache: 'no-store' })
    } catch {
      res = null
    }
    if (!alive.current) return
    if (res === null) return finish({ state: 'error', detail: GENERIC_ERROR })
    if (!res.ok) {
      const { detail } = await readErrorBody(res)
      if (alive.current) finish({ state: 'error', detail })
      return
    }
    let blob: Blob
    try {
      blob = await res.blob()
    } catch {
      if (alive.current) finish({ state: 'error', detail: GENERIC_ERROR })
      return
    }
    if (!alive.current) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    held.current = null
    removeJob(jobId)
    finish({ state: 'saved', filename })
  }

  async function start(retried = false) {
    if (!retried) {
      if (busy.current || picked === null) return
      busy.current = true
      setJob({ state: 'starting' })
    }
    const sel = selectionOf(target, wave, age)
    if (!(await refreshSession())) {
      if (alive.current) finish({ state: 'error', detail: SIGN_IN })
      return
    }
    if (!alive.current) return
    let res: Response
    try {
      res = await fetch(jobUrl(sel, picked as string, defaultKey), {
        method: 'POST',
        cache: 'no-store',
      })
    } catch {
      if (alive.current) finish({ state: 'error', detail: GENERIC_ERROR })
      return
    }
    if (res.ok) {
      let jobId: string | null = null
      try {
        const body = (await res.json()) as { job_id?: unknown }
        jobId = typeof body.job_id === 'string' ? body.job_id : null
      } catch {
        jobId = null
      }
      if (!alive.current) {
        if (jobId !== null) removeJob(jobId)
        return
      }
      if (jobId === null) return finish({ state: 'error', detail: GENERIC_ERROR })
      return follow(jobId)
    }
    const refusal = classifyStartError(res.status, await readErrorBody(res))
    if (!alive.current) return
    if (refusal.kind === 'refresh' && !retried) return start(true)
    if (refusal.kind === 'resume')
      return setJob({ state: 'offer', jobId: refusal.jobId, detail: refusal.detail })
    finish({ state: 'error', detail: refusal.detail })
  }

  function resume(jobId: string) {
    follow(jobId)
  }

  function cancelOffered(jobId: string) {
    removeJob(jobId)
    finish({ state: 'idle' })
  }

  function checkAgain() {
    failures.current = createFailureCounter()
    startedAt.current = Date.now()
    setJob({ state: 'running', line: phaseLine({ phase: 'queued', done: 0, total: 0 }) })
    void poll()
  }

  const title = isExperiment ? `experiment ${target.experimentId}` : `scan ${target.scanId}`

  return (
    <Dialog open onClose={onClose} aria-labelledby={headingId} maxWidth="sm" fullWidth>
      <div className="space-y-4 p-6 text-sm text-stone-700">
        <h2 id={headingId} className="text-lg text-stone-900">
          Download traits <span className="text-stone-500">· {title}</span>
        </h2>

        {isExperiment && (
          <div className="flex flex-wrap gap-4">
            <div className="flex flex-col gap-1">
              <label htmlFor={waveId} className="text-xs text-stone-500">
                Wave
              </label>
              <select
                id={waveId}
                value={String(wave)}
                disabled={jobActive}
                onChange={(e) => setWave(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                className="rounded-md border border-stone-300 px-2 py-1"
              >
                <option value="all">All</option>
                {target.waves.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={ageId} className="text-xs text-stone-500">
                Plant age
              </label>
              <select
                id={ageId}
                value={String(age)}
                disabled={jobActive}
                onChange={(e) => setAge(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                className="rounded-md border border-stone-300 px-2 py-1"
              >
                <option value="all">All</option>
                {target.ages.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {listing.state === 'loading' && <p className="text-stone-500">Listing trait recipes…</p>}
        {listing.state === 'error' && (
          <div
            role="alert"
            className="flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-red-800"
          >
            <span>{listing.detail}</span>
            <button
              type="button"
              onClick={retryListing}
              className="shrink-0 underline hover:no-underline"
            >
              Retry
            </button>
          </div>
        )}
        {empty !== null && <p className="text-stone-500">{empty}</p>}

        {rows.length > 0 && (
          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs text-stone-500">Recipe (one per file)</legend>
            {rows.map((row) => (
              <label
                key={row.recipe_key}
                className="flex items-start gap-2 rounded-md border border-stone-200 p-2"
              >
                <input
                  type="radio"
                  name={recipeName}
                  value={row.recipe_key}
                  checked={picked === row.recipe_key}
                  disabled={jobActive}
                  onChange={() => setPicked(row.recipe_key)}
                  className="mt-1"
                />
                <span className="space-y-0.5">
                  <span className="block">
                    <span className="font-mono">{recipeLabel(row.recipe_key)}</span> ·{' '}
                    {row.recipe_kind} · {row.n_scans} of {nSelected} scans
                    {row.is_default && (
                      <span className="ml-2 rounded bg-stone-100 px-1.5 py-0.5 text-xs text-stone-600">
                        default
                      </span>
                    )}
                  </span>
                  {describeRecipe(row).map((line) => (
                    <span key={line} className="block text-xs text-stone-500">
                      {line}
                    </span>
                  ))}
                </span>
              </label>
            ))}
          </fieldset>
        )}

        {note !== null && (
          <p
            data-testid="fewer-scans-note"
            className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900"
          >
            The default is the newest recipe and covers {rows.find((r) => r.is_default)?.n_scans} of{' '}
            {nSelected} scans. <span className="font-mono">{note.label}</span> covers {note.nScans};
            pick it to export more scans.
          </p>
        )}

        {job.state === 'running' && (
          <p role="status" className="text-stone-600">
            {job.line}
          </p>
        )}
        {job.state === 'saved' && (
          <p role="status" className="text-lime-800">
            Saved {job.filename}.
          </p>
        )}
        {job.state === 'offer' && (
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
            <span>{job.detail}.</span>
            <button
              type="button"
              onClick={() => resume(job.jobId)}
              className="underline hover:no-underline"
            >
              Resume
            </button>
            <button
              type="button"
              onClick={() => cancelOffered(job.jobId)}
              className="underline hover:no-underline"
            >
              Cancel export
            </button>
          </div>
        )}
        {job.state === 'paused' && (
          <div
            role="alert"
            className="flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-red-800"
          >
            <span>{POLL_FAILED}</span>
            <button
              type="button"
              onClick={checkAgain}
              className="shrink-0 underline hover:no-underline"
            >
              Check again
            </button>
          </div>
        )}
        {job.state === 'error' && (
          <div
            role="alert"
            className="flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-red-800"
          >
            <span>{job.detail}</span>
            <button
              type="button"
              onClick={() => void start()}
              className="shrink-0 underline hover:no-underline"
            >
              Retry
            </button>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 pt-2">
          <a
            href={HELP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-lime-700 underline hover:no-underline"
          >
            What&apos;s in this file?
          </a>
          <div className="flex gap-3">
            <button
              type="button"
              autoFocus
              onClick={onClose}
              className="rounded-md px-3 py-1.5 text-stone-600 hover:bg-stone-100"
            >
              Close
            </button>
            <button
              type="button"
              onClick={() => void start()}
              disabled={!canDownload}
              className="rounded-md bg-lime-700 px-3 py-1.5 text-white hover:bg-lime-800 disabled:cursor-not-allowed disabled:bg-stone-300"
            >
              Download
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}
