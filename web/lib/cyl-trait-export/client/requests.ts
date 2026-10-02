/**
 * The Download traits dialog's requests and how it reads their errors (design D8;
 * spec "Trait download dialog job lifecycle"). Client-safe.
 */

import type { JobView } from '../jobs'
import { MIN_SESSION_SECONDS } from '../limits'
import type { RecipeKind, RecipeRow } from '../recipes'
import type { FilterValue } from './recipe-view'

export type DialogSelection =
  | { experiment: number; wave: FilterValue; age: FilterValue }
  | { scan: number }

export const BASE = '/api/cyl/trait-export'

/** The detail the job route gives a token with too little life left; the dialog retries once. */
export const SESSION_TOO_SHORT = 'session expires too soon'

/** Shown when an error body has no readable `detail`. */
export const GENERIC_ERROR = 'The export could not be completed. Please try again.'

export type ErrorBody = { detail: string; job_id?: string }

export type StartError =
  | { kind: 'resume'; jobId: string; detail: string }
  | { kind: 'busy'; detail: string }
  | { kind: 'refresh'; detail: string }
  | { kind: 'error'; detail: string }

/** The route's selection parameters; "All" leaves a filter out. */
function selectionParams(sel: DialogSelection): URLSearchParams {
  const p = new URLSearchParams()
  if ('scan' in sel) {
    p.set('scan', String(sel.scan))
    return p
  }
  p.set('experiment', String(sel.experiment))
  if (sel.wave !== 'all') p.set('wave', String(sel.wave))
  if (sel.age !== 'all') p.set('age', String(sel.age))
  return p
}

export function recipesUrl(sel: DialogSelection): string {
  return `${BASE}/recipes?${selectionParams(sel)}`
}

/** `chosen` is `default` exactly when the picked key is the listing's default. */
export function jobUrl(sel: DialogSelection, recipeKey: string, defaultKey: string | null): string {
  const p = selectionParams(sel)
  p.set('recipe', recipeKey)
  p.set('chosen', recipeKey === defaultKey ? 'default' : 'user')
  return `${BASE}/jobs?${p}`
}

export function jobStatusUrl(jobId: string): string {
  return `${BASE}/jobs/${encodeURIComponent(jobId)}`
}

export function downloadUrl(jobId: string): string {
  return `${jobStatusUrl(jobId)}/download`
}

/** The body's `detail` (and `job_id`), or the generic message when it is not JSON. */
export async function readErrorBody(res: { json: () => Promise<unknown> }): Promise<ErrorBody> {
  let body: unknown
  try {
    body = await res.json()
  } catch {
    return { detail: GENERIC_ERROR }
  }
  const b = (body ?? {}) as { detail?: unknown; job_id?: unknown }
  if (typeof b.detail !== 'string' || b.detail === '') return { detail: GENERIC_ERROR }
  return isJobId(b.job_id) ? { detail: b.detail, job_id: b.job_id } : { detail: b.detail }
}

/** What a refused job start means for the dialog. */
export function classifyStartError(status: number, body: ErrorBody): StartError {
  if (status === 429) {
    return body.job_id
      ? { kind: 'resume', jobId: body.job_id, detail: body.detail }
      : { kind: 'busy', detail: body.detail }
  }
  if (status === 401 && body.detail === SESSION_TOO_SHORT)
    return { kind: 'refresh', detail: body.detail }
  return { kind: 'error', detail: body.detail }
}

export type Listing = { nSelected: number; rows: RecipeRow[] }

/** Saved under this name when a ready job's filename is missing or not a `<stem>.zip`. */
export const FALLBACK_FILENAME = 'traits.zip'

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const ZIP_NAME = /^[a-z0-9_-]+\.zip$/
const KINDS: RecipeKind[] = ['pipeline', 'legacy', 'unattributed']
const STATUSES: JobView['status'][] = ['running', 'ready', 'failed', 'cancelled']

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

function isRow(v: unknown): v is RecipeRow {
  return (
    isObject(v) &&
    typeof v.recipe_key === 'string' &&
    KINDS.includes(v.recipe_kind as RecipeKind) &&
    isCount(v.n_scans) &&
    typeof v.is_default === 'boolean' &&
    (v.definition === null || isObject(v.definition))
  )
}

/** The listing route's body, or null for anything else. */
export function parseListing(body: unknown): Listing | null {
  if (!isObject(body) || !isCount(body.n_selected) || !Array.isArray(body.rows)) return null
  if (!body.rows.every(isRow)) return null
  return { nSelected: body.n_selected, rows: body.rows }
}

/** The status route's body, or null; an unsafe filename is dropped. */
export function parseJobView(body: unknown): JobView | null {
  if (!isObject(body)) return null
  const { status, phase, done, total, detail, filename } = body
  if (!STATUSES.includes(status as JobView['status'])) return null
  if (typeof phase !== 'string' || !isCount(done) || !isCount(total)) return null
  const view: JobView = { status: status as JobView['status'], phase, done, total }
  if (typeof detail === 'string' && detail !== '') view.detail = detail
  if (typeof filename === 'string' && ZIP_NAME.test(filename)) view.filename = filename
  return view
}

/** A job id as the server issues them (a lowercase UUID). */
export function isJobId(v: unknown): v is string {
  return typeof v === 'string' && JOB_ID.test(v)
}

/** A `<stem>.zip` name, or the fallback. */
export function safeFilename(v: unknown): string {
  return typeof v === 'string' && ZIP_NAME.test(v) ? v : FALLBACK_FILENAME
}

/** True when the session would fail the job route's floor (or has no expiry). */
export function sessionNeedsRefresh(expiresAtSec: number | undefined, nowMs: number): boolean {
  return expiresAtSec === undefined || expiresAtSec - nowMs / 1000 < MIN_SESSION_SECONDS
}

/** A refresh the sign-in service refused (4xx) means signed out; anything else may be retried. */
export function refreshFailureKind(error: unknown): 'signin' | 'retry' {
  const status = isObject(error) ? error.status : undefined
  return typeof status === 'number' && status >= 400 && status < 500 ? 'signin' : 'retry'
}
