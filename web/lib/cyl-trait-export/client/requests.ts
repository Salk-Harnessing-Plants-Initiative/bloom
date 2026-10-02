/**
 * The Download traits dialog's requests and how it reads their errors (design D8;
 * spec "Trait download dialog job lifecycle"). Client-safe.
 */

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
  return typeof b.job_id === 'string'
    ? { detail: b.detail, job_id: b.job_id }
    : { detail: b.detail }
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
