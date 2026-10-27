'use client'

/**
 * The Download traits dialog (spec "Trait download dialog recipe list" and "Trait
 * download dialog job lifecycle"; design D8).
 *
 * - It lists the selection's recipes, the default preselected, and re-lists 500 ms
 *   after a filter change. A filter change makes the previous listing stale at once
 *   (its request is aborted and its answer dropped), and the route answers `499` to a
 *   listing the same user replaced, so a `499` is shown only when nothing newer was
 *   sent here.
 * - Download, and Retry, start a job only from the current listing. The session is
 *   refreshed only when it is near the job route's floor: a refresh that fails for any
 *   reason auth-js does not retry signs the user out of all of Bloom. Polls are chained
 *   timeouts, so they never overlap, and an answer for a job no longer followed is
 *   dropped. A ready zip is saved under the job's filename and kept for "Save again"
 *   while the dialog is open; a job this dialog started is then deleted to free server
 *   memory.
 * - The dialog is mounted only while open. Unmounting stops polling, aborts the
 *   listing, poll and download requests, and deletes any job it started (not one it
 *   only resumed: that is often another tab's export), including one whose start
 *   answers after the close; the start request is not aborted, so that job's id
 *   still arrives.
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
  countLabel,
  describeRecipe,
  emptyState,
  fewerScansNote,
  prefill,
  recipeLabel,
  selectionTitle,
  type FilterValue,
} from '@/lib/cyl-trait-export/client/recipe-view'
import {
  GENERIC_ERROR,
  classifyStartError,
  downloadUrl,
  isJobId,
  jobStatusUrl,
  jobUrl,
  parseJobView,
  parseListing,
  readErrorBody,
  recipesUrl,
  refreshFailureKind,
  safeFilename,
  sessionNeedsRefresh,
  type DialogSelection,
} from '@/lib/cyl-trait-export/client/requests'
import type { RecipeRow } from '@/lib/cyl-trait-export/recipes'
import { createClientSupabaseClient } from '@/lib/supabase/client'

/** What the dialog exports: an experiment (with the page's wave/age and loaded lists) or one scan. */
export type ExportTarget =
  | { experimentId: number; wave?: number; age?: number; waves: number[]; ages: number[] }
  | { scanId: number }

export const HELP_URL =
  'https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/_WIKI/SUPABASE/trait-recipes.md#using-a-trait-export'
export const SIGN_IN = 'Your session has ended. Sign in again to download traits.'
export const SESSION_RETRY = 'Could not reach the sign-in service. Please try again.'
export const NOT_ON_SERVER =
  'The export is no longer on the server: it was already downloaded (perhaps in another tab), it expired, a newer export replaced it, or the server restarted. Please retry.'
export const LIST_FAILED = 'Could not list the trait recipes. Please try again.'
export const LISTING_REPLACED =
  'Another Download traits window started listing, so this one stopped. Retry to list again.'
export const CANCELLED = 'The export was cancelled.'
export const POLL_FAILED = "Could not check the export's progress."
const OFFER =
  'You already have an export running, possibly started in another tab for a different selection; its file name will say which. Resume it to save it here, or cancel it to start this one.'
const RESUMED_NOTE = ' This is the export you resumed; its selection may differ from the one above.'

const DEBOUNCE_MS = 500

type Listing =
  | { state: 'loading' }
  | { state: 'ready'; nSelected: number; rows: RecipeRow[] }
  | { state: 'error'; detail: string }

type Job =
  | { state: 'idle' }
  | { state: 'starting' }
  | { state: 'running'; line: string }
  | { state: 'offer'; jobId: string; cancelling: boolean }
  | { state: 'paused' }
  | { state: 'saved'; filename: string; resumed: boolean }
  | { state: 'error'; detail: string }

/** Refresh only when needed; `force` after a "session expires too soon" 401. */
async function ensureSession(force: boolean): Promise<'ok' | 'signin' | 'retry'> {
  try {
    const auth = createClientSupabaseClient().auth
    if (!force) {
      const { data } = await auth.getSession()
      if (!sessionNeedsRefresh(data.session?.expires_at, Date.now())) return 'ok'
    }
    const { error } = await auth.refreshSession()
    return error ? refreshFailureKind(error) : 'ok'
  } catch {
    return 'retry'
  }
}

function removeJob(jobId: string): Promise<void> {
  return fetch(jobStatusUrl(jobId), { method: 'DELETE', keepalive: true })
    .then((res) => {
      if (!res.ok && res.status !== 404)
        console.warn(`trait export: DELETE ${jobId} answered ${res.status}`)
    })
    .catch((e) => console.warn(`trait export: DELETE ${jobId} failed`, e))
}

function selectionOf(target: ExportTarget, wave: FilterValue, age: FilterValue): DialogSelection {
  return 'scanId' in target
    ? { scan: target.scanId }
    : { experiment: target.experimentId, wave, age }
}

function saveAs(url: string, filename: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
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
  /** The filters of the latest listing scheduled; a change from them waits for the debounce. */
  const scheduledKey = useRef<string | null>(null)
  const guard = useRef(createLatestGuard())
  const listAbort = useRef<AbortController | null>(null)
  /** Aborts the poll and download requests on close. */
  const jobAbort = useRef<AbortController | null>(null)
  /** The recipe the user picked, if any; an automatic pick follows the default instead. */
  const userPick = useRef<string | null>(null)
  /** The job being followed, and whether this dialog started it (only those are deleted). */
  const held = useRef<{ id: string; owned: boolean } | null>(null)
  const busy = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const startedAt = useRef(0)
  const failures = useRef(createFailureCounter())
  /** The saved zip's object URL, kept for "Save again" and revoked on unmount. */
  const saved = useRef<{ url: string; filename: string } | null>(null)

  function dropSaved() {
    if (saved.current) URL.revokeObjectURL(saved.current.url)
    saved.current = null
  }

  useEffect(() => {
    alive.current = true
    jobAbort.current = new AbortController()
    return () => {
      alive.current = false
      clearTimeout(timer.current)
      listAbort.current?.abort()
      jobAbort.current?.abort()
      if (held.current?.owned) void removeJob(held.current.id)
      held.current = null
      dropSaved()
    }
  }, [])

  useEffect(() => {
    const sel = selectionOf(target, wave, age)
    // The previous listing is stale from this moment, not from when the next one starts.
    const n = guard.current.next()
    listAbort.current?.abort()
    const ctrl = new AbortController()
    listAbort.current = ctrl
    setListing({ state: 'loading' })

    async function list() {
      const current = () => alive.current && guard.current.isCurrent(n)
      const fail = (detail: string) => {
        if (current()) setListing({ state: 'error', detail })
      }
      let res: Response
      try {
        res = await fetch(recipesUrl(sel), { signal: ctrl.signal, cache: 'no-store' })
      } catch {
        return fail(LIST_FAILED)
      }
      if (!current()) return
      if (res.status === 499) return fail(LISTING_REPLACED)
      if (!res.ok) return fail((await readErrorBody(res, LIST_FAILED)).detail)
      let body: unknown
      try {
        body = await res.json()
      } catch {
        return fail(LIST_FAILED)
      }
      if (!current()) return
      const parsed = parseListing(body)
      if (parsed === null) return fail(LIST_FAILED)
      const kept = parsed.rows.some((r) => r.recipe_key === userPick.current)
      if (!kept) userPick.current = null
      setListing({ state: 'ready', nSelected: parsed.nSelected, rows: parsed.rows })
      setPicked(kept ? userPick.current : parsed.rows.find((r) => r.is_default)?.recipe_key ?? null)
    }

    // The first listing, a Retry and a StrictMode re-run list at once; a filter change waits.
    const key = `${wave}|${age}`
    const immediate = scheduledKey.current === null || scheduledKey.current === key
    scheduledKey.current = key
    if (immediate) {
      void list()
      return
    }
    const t = setTimeout(() => void list(), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [target, wave, age, relist])

  const rows = listing.state === 'ready' ? listing.rows : []
  const nSelected = listing.state === 'ready' ? listing.nSelected : 0
  const empty = listing.state === 'ready' ? emptyState(listing.nSelected, listing.rows) : null
  const defaultRow = rows.find((r) => r.is_default)
  const defaultKey = defaultRow?.recipe_key ?? null
  const note = fewerScansNote(rows)
  const jobActive = ['starting', 'running', 'offer', 'paused'].includes(job.state)
  const listingReady =
    listing.state === 'ready' &&
    empty === null &&
    picked !== null &&
    rows.some((r) => r.recipe_key === picked)
  const canDownload = listingReady && !jobActive

  function retryListing() {
    setRelist((r) => r + 1)
  }

  function changeFilter(set: (v: FilterValue) => void, raw: string) {
    set(raw === 'all' ? 'all' : Number(raw))
    if (job.state === 'error' || job.state === 'saved') {
      dropSaved()
      setJob({ state: 'idle' })
    }
  }

  function schedule() {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void poll(), pollDelay(Date.now() - startedAt.current))
  }

  function follow(jobId: string, owned: boolean) {
    const previous = held.current
    if (previous !== null && previous.id !== jobId && previous.owned) void removeJob(previous.id)
    held.current = { id: jobId, owned: owned || (previous?.id === jobId && previous.owned) }
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
    const followed = held.current
    if (followed === null) return
    const jobId = followed.id
    const stillFollowing = () => alive.current && held.current?.id === jobId
    let res: Response | null
    try {
      res = await fetch(jobStatusUrl(jobId), {
        cache: 'no-store',
        signal: jobAbort.current?.signal,
      })
    } catch {
      res = null
    }
    if (!stillFollowing()) return
    if (res?.status === 404) {
      held.current = null
      return finish({ state: 'error', detail: NOT_ON_SERVER })
    }
    if (res?.status === 401) return finish({ state: 'error', detail: SIGN_IN })
    let view = null
    if (res?.ok) {
      try {
        view = parseJobView(await res.json())
      } catch {
        view = null
      }
    }
    if (!stillFollowing()) return
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
      await save(jobId, followed.owned, safeFilename(view.filename))
    } else if (view.status === 'cancelled') {
      held.current = null
      finish({ state: 'error', detail: CANCELLED })
    } else {
      finish({ state: 'error', detail: view.detail ?? GENERIC_ERROR })
    }
  }

  async function save(jobId: string, owned: boolean, filename: string) {
    const stillFollowing = () => alive.current && held.current?.id === jobId
    setJob({ state: 'running', line: phaseLine({ phase: 'done', done: 0, total: 0 }) })
    let res: Response | null
    try {
      res = await fetch(downloadUrl(jobId), { cache: 'no-store', signal: jobAbort.current?.signal })
    } catch {
      res = null
    }
    if (!stillFollowing()) return
    if (res === null) return finish({ state: 'error', detail: GENERIC_ERROR })
    if (res.status === 404) {
      held.current = null
      return finish({ state: 'error', detail: NOT_ON_SERVER })
    }
    if (!res.ok) {
      const { detail } = await readErrorBody(res)
      if (stillFollowing()) finish({ state: 'error', detail })
      return
    }
    let blob: Blob
    try {
      blob = await res.blob()
    } catch {
      if (stillFollowing()) finish({ state: 'error', detail: GENERIC_ERROR })
      return
    }
    if (!stillFollowing()) return
    dropSaved()
    const url = URL.createObjectURL(blob)
    saved.current = { url, filename }
    saveAs(url, filename)
    held.current = null
    if (owned) void removeJob(jobId)
    finish({ state: 'saved', filename, resumed: !owned })
  }

  async function start(retried = false) {
    if (!retried) {
      if (busy.current || !listingReady) return
      busy.current = true
      setJob({ state: 'starting' })
    }
    const recipe = picked as string
    const sel = selectionOf(target, wave, age)
    const session = await ensureSession(retried)
    if (!alive.current) return
    if (session !== 'ok') {
      return finish({ state: 'error', detail: session === 'signin' ? SIGN_IN : SESSION_RETRY })
    }
    let res: Response
    try {
      res = await fetch(jobUrl(sel, recipe, defaultKey), { method: 'POST', cache: 'no-store' })
    } catch {
      if (alive.current) finish({ state: 'error', detail: GENERIC_ERROR })
      return
    }
    if (res.ok) {
      let jobId: string | null = null
      try {
        const body = (await res.json()) as { job_id?: unknown }
        jobId = isJobId(body.job_id) ? body.job_id : null
      } catch {
        jobId = null
      }
      if (!alive.current) {
        if (jobId !== null) void removeJob(jobId)
        return
      }
      if (jobId === null) return finish({ state: 'error', detail: GENERIC_ERROR })
      return follow(jobId, true)
    }
    const refusal = classifyStartError(res.status, await readErrorBody(res))
    if (!alive.current) return
    if (refusal.kind === 'refresh' && !retried) return start(true)
    if (refusal.kind === 'resume') {
      // Our own job (e.g. still held after a poll 401): keep following it as ours.
      if (held.current?.id === refusal.jobId) return follow(refusal.jobId, held.current.owned)
      return setJob({ state: 'offer', jobId: refusal.jobId, cancelling: false })
    }
    finish({ state: 'error', detail: refusal.detail })
  }

  async function cancelOffered(jobId: string) {
    setJob((j) => (j.state === 'offer' && j.jobId === jobId ? { ...j, cancelling: true } : j))
    await removeJob(jobId)
    if (!alive.current) return
    busy.current = false
    setJob((j) => (j.state === 'offer' && j.jobId === jobId ? { state: 'idle' } : j))
  }

  function checkAgain() {
    failures.current = createFailureCounter()
    startedAt.current = Date.now()
    setJob({ state: 'running', line: phaseLine({ phase: 'queued', done: 0, total: 0 }) })
    void poll()
  }

  function close(_event: unknown, reason?: string) {
    if (jobActive && (reason === 'backdropClick' || reason === 'escapeKeyDown')) return
    onClose()
  }

  const statusLine =
    job.state === 'running'
      ? job.line
      : job.state === 'saved'
        ? `Download started: ${job.filename}.${job.resumed ? RESUMED_NOTE : ''}`
        : ''

  return (
    <Dialog open onClose={close} aria-labelledby={headingId} maxWidth="sm" fullWidth>
      <div className="space-y-4 p-6 text-sm text-stone-700">
        <h2 id={headingId} className="text-lg text-stone-900">
          Download traits{' '}
          <span className="text-stone-500">· {selectionTitle(target, wave, age)}</span>
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
                onChange={(e) => changeFilter(setWave, e.target.value)}
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
                onChange={(e) => changeFilter(setAge, e.target.value)}
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
            <legend className="mb-1 text-xs text-stone-500">Recipe</legend>
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
                  onChange={() => {
                    userPick.current = row.recipe_key
                    setPicked(row.recipe_key)
                  }}
                  className="mt-1"
                />
                <span className="space-y-0.5">
                  <span className="block">
                    <span className="font-mono" title={row.recipe_key}>
                      {recipeLabel(row.recipe_key)}
                    </span>{' '}
                    · {row.recipe_kind} · {countLabel(row.n_scans, nSelected)}
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

        {note !== null && defaultRow !== undefined && (
          <p
            data-testid="fewer-scans-note"
            className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900"
          >
            The default is the most recently added recipe and covers{' '}
            {countLabel(defaultRow.n_scans, nSelected)}.{' '}
            <span className="font-mono">{note.label}</span> covers{' '}
            {countLabel(note.nScans, nSelected)}
            {picked === note.key ? '. ' : '; pick it to export more scans. '}
            {note.kind === 'legacy' ? 'Its models and code were not recorded. ' : ''}
            Recipes differ in models and trait columns, so use one recipe per analysis.
          </p>
        )}

        <p role="status" className="text-stone-600">
          {statusLine}
        </p>
        {job.state === 'saved' && saved.current && (
          <button
            type="button"
            onClick={() => saved.current && saveAs(saved.current.url, saved.current.filename)}
            className="text-lime-700 underline hover:no-underline"
          >
            Save again
          </button>
        )}
        {job.state === 'offer' && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900"
          >
            <span>{OFFER}</span>
            <button
              type="button"
              disabled={job.cancelling}
              onClick={() => follow(job.jobId, false)}
              className="underline hover:no-underline disabled:cursor-not-allowed disabled:text-stone-400"
            >
              Resume
            </button>
            <button
              type="button"
              disabled={job.cancelling}
              onClick={() => void cancelOffered(job.jobId)}
              className="underline hover:no-underline disabled:cursor-not-allowed disabled:text-stone-400"
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
              disabled={!listingReady}
              className="shrink-0 underline hover:no-underline disabled:cursor-not-allowed disabled:text-stone-400"
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
