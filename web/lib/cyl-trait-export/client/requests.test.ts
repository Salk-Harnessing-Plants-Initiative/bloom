/** The dialog's requests and error reading (design D8; spec "Trait download dialog job lifecycle"). */

import {
  AuthApiError,
  AuthRetryableFetchError,
  AuthSessionMissingError,
  AuthUnknownError,
} from '@supabase/supabase-js'
import { describe, expect, it } from 'vitest'

import {
  FALLBACK_FILENAME,
  GENERIC_ERROR,
  SESSION_TOO_SHORT,
  classifyStartError,
  downloadUrl,
  jobStatusUrl,
  jobUrl,
  isJobId,
  parseJobView,
  parseListing,
  readErrorBody,
  recipesUrl,
  refreshFailureKind,
  safeFilename,
  sessionNeedsRefresh,
} from './requests'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const JOB = '0f8fad5b-d9cb-469f-a165-70867728950e'

function query(url: string): Record<string, string> {
  const u = new URL(url, 'http://localhost')
  return Object.fromEntries(u.searchParams.entries())
}

describe('recipesUrl', () => {
  it('sends an experiment with only the filters that are not "All"', () => {
    const url = recipesUrl({ experiment: 3313, wave: 'all', age: 'all' })
    expect(new URL(url, 'http://localhost').pathname).toBe('/api/cyl/trait-export/recipes')
    expect(query(url)).toEqual({ experiment: '3313' })
    expect(query(recipesUrl({ experiment: 1, wave: 2, age: 'all' }))).toEqual({
      experiment: '1',
      wave: '2',
    })
    expect(query(recipesUrl({ experiment: 1, wave: 'all', age: 7 }))).toEqual({
      experiment: '1',
      age: '7',
    })
  })

  it('sends wave 0 and age 0 as values', () => {
    expect(query(recipesUrl({ experiment: 1, wave: 0, age: 0 }))).toEqual({
      experiment: '1',
      wave: '0',
      age: '0',
    })
  })

  it('sends a scan alone', () => {
    expect(query(recipesUrl({ scan: 577 }))).toEqual({ scan: '577' })
  })
})

describe('jobUrl', () => {
  it('carries the selection, the recipe and chosen=default when the pick is the default', () => {
    const url = jobUrl({ experiment: 1, wave: 2, age: 'all' }, K, K)
    expect(new URL(url, 'http://localhost').pathname).toBe('/api/cyl/trait-export/jobs')
    expect(query(url)).toEqual({ experiment: '1', wave: '2', recipe: K, chosen: 'default' })
  })

  it('sends chosen=user for any other pick, and a legacy key arrives decoded', () => {
    const url = jobUrl({ scan: 577 }, 'legacy:5', K)
    expect(query(url)).toEqual({ scan: '577', recipe: 'legacy:5', chosen: 'user' })
    expect(query(jobUrl({ scan: 577 }, 'legacy:5', null)).chosen).toBe('user')
  })
})

describe('job URLs', () => {
  it('address one job and its download', () => {
    expect(jobStatusUrl(JOB)).toBe(`/api/cyl/trait-export/jobs/${JOB}`)
    expect(downloadUrl(JOB)).toBe(`/api/cyl/trait-export/jobs/${JOB}/download`)
  })
})

describe('readErrorBody', () => {
  it('reads detail and job_id', async () => {
    await expect(
      readErrorBody({
        json: async () => ({ detail: 'you already have an export running', job_id: JOB }),
      })
    ).resolves.toEqual({ detail: 'you already have an export running', job_id: JOB })
    await expect(
      readErrorBody({ json: async () => ({ detail: 'no such scan' }) })
    ).resolves.toEqual({
      detail: 'no such scan',
    })
  })

  it('gives the generic message for a body that is not JSON or has no detail', async () => {
    await expect(
      readErrorBody({ json: async () => Promise.reject(new SyntaxError('bad')) })
    ).resolves.toEqual({
      detail: GENERIC_ERROR,
    })
    await expect(readErrorBody({ json: async () => ({ message: 'x' }) })).resolves.toEqual({
      detail: GENERIC_ERROR,
    })
    await expect(readErrorBody({ json: async () => ({ detail: 7, job_id: 3 }) })).resolves.toEqual({
      detail: GENERIC_ERROR,
    })
  })
})

describe('classifyStartError', () => {
  it('offers to resume a 429 that names the user’s own job, and calls one without a job busy', () => {
    expect(
      classifyStartError(429, { detail: 'you already have an export running', job_id: JOB })
    ).toEqual({
      kind: 'resume',
      jobId: JOB,
      detail: 'you already have an export running',
    })
    expect(classifyStartError(429, { detail: 'the server is busy' })).toEqual({
      kind: 'busy',
      detail: 'the server is busy',
    })
  })

  it('refreshes only for the session-too-short 401', () => {
    expect(SESSION_TOO_SHORT).toBe('session expires too soon')
    expect(classifyStartError(401, { detail: SESSION_TOO_SHORT })).toEqual({
      kind: 'refresh',
      detail: SESSION_TOO_SHORT,
    })
    expect(classifyStartError(401, { detail: 'Sign in to download traits.' })).toEqual({
      kind: 'error',
      detail: 'Sign in to download traits.',
    })
  })

  it('treats every other refusal as an error with its detail', () => {
    for (const status of [403, 404, 409, 422, 499, 502, 503]) {
      expect(classifyStartError(status, { detail: `d${status}` })).toEqual({
        kind: 'error',
        detail: `d${status}`,
      })
    }
    expect(classifyStartError(409, { detail: 'x', job_id: JOB })).toEqual({
      kind: 'error',
      detail: 'x',
    })
  })
})

const ROW = {
  recipe_key: 'legacy:5',
  recipe_kind: 'legacy',
  recipe_key_version: null,
  definition: { source_id: 5, source_name: 'five' },
  n_scans: 60,
  newest_source_id: 5,
  is_default: true,
}

describe('parseListing', () => {
  it('reads a listing', () => {
    expect(parseListing({ n_selected: 100, rows: [ROW] })).toEqual({ nSelected: 100, rows: [ROW] })
    expect(parseListing({ n_selected: 0, rows: [] })).toEqual({ nSelected: 0, rows: [] })
  })

  it('refuses anything that is not a listing', () => {
    for (const body of [
      null,
      [],
      'x',
      { rows: [ROW] },
      { n_selected: '1', rows: [ROW] },
      { n_selected: 1, rows: 'x' },
      { n_selected: 1, rows: [{ ...ROW, recipe_key: 5 }] },
      { n_selected: 1, rows: [{ ...ROW, recipe_kind: 'other' }] },
      { n_selected: 1, rows: [{ ...ROW, n_scans: '60' }] },
      { n_selected: 1, rows: [{ ...ROW, is_default: 'yes' }] },
      { n_selected: 1, rows: [{ ...ROW, definition: 'x' }] },
    ]) {
      expect([body, parseListing(body)]).toEqual([body, null])
    }
  })
})

describe('parseJobView', () => {
  it('reads a job view, keeping a safe filename', () => {
    expect(parseJobView({ status: 'running', phase: 'traits', done: 3, total: 10 })).toEqual({
      status: 'running',
      phase: 'traits',
      done: 3,
      total: 10,
    })
    expect(
      parseJobView({
        status: 'ready',
        phase: 'done',
        done: 1,
        total: 1,
        filename: 'exp_legacy-5_20261002.zip',
      })
    ).toMatchObject({ status: 'ready', filename: 'exp_legacy-5_20261002.zip' })
    expect(
      parseJobView({ status: 'failed', phase: 'traits', done: 1, total: 4, detail: 'boom' })
    ).toMatchObject({
      detail: 'boom',
    })
  })

  it('refuses an unknown status or missing counts, and drops an unsafe filename', () => {
    expect(parseJobView(null)).toBeNull()
    expect(parseJobView({ status: 'queued', phase: 'x', done: 0, total: 0 })).toBeNull()
    expect(parseJobView({ status: 'running', phase: 'traits', done: '3', total: 10 })).toBeNull()
    expect(parseJobView({ status: 'running', done: 3, total: 10 })).toBeNull()
    expect(
      parseJobView({ status: 'ready', phase: 'done', done: 1, total: 1, filename: '../x.zip' })
    ).not.toHaveProperty('filename')
  })
})

describe('isJobId and safeFilename', () => {
  it('accepts only a lowercase UUID', () => {
    expect(isJobId(JOB)).toBe(true)
    for (const v of ['..', '.', JOB.toUpperCase(), `${JOB}/x`, 7, null])
      expect(isJobId(v)).toBe(false)
  })

  it('keeps a <stem>.zip name and falls back otherwise', () => {
    expect(safeFilename('diversity-screen_legacy-5_20261002.zip')).toBe(
      'diversity-screen_legacy-5_20261002.zip'
    )
    expect(FALLBACK_FILENAME).toBe('traits.zip')
    for (const v of ['../x.zip', 'x.exe', 'X.zip', '', undefined, 3])
      expect(safeFilename(v)).toBe('traits.zip')
  })

  it('drops a job_id that is not a UUID from an error body', async () => {
    await expect(
      readErrorBody({ json: async () => ({ detail: 'busy', job_id: '..' }) })
    ).resolves.toEqual({
      detail: 'busy',
    })
  })
})

describe('sessionNeedsRefresh', () => {
  const now = Date.parse('2026-10-02T12:00:00Z')
  const s = now / 1000
  it('refreshes when the session has under MIN_SESSION_SECONDS left, or no expiry', () => {
    expect(sessionNeedsRefresh(undefined, now)).toBe(true)
    expect(sessionNeedsRefresh(s + 1799, now)).toBe(true)
    expect(sessionNeedsRefresh(s + 1800, now)).toBe(false)
    expect(sessionNeedsRefresh(s + 3600, now)).toBe(false)
  })
})

describe('refreshFailureKind', () => {
  it('is retry only for what auth-js retries (it keeps the session then)', () => {
    expect(refreshFailureKind(new AuthRetryableFetchError('fetch failed', 0))).toBe('retry')
    expect(refreshFailureKind(new AuthRetryableFetchError('bad gateway', 503))).toBe('retry')
  })

  it('is a sign-in for every other auth-js error (auth-js has removed the session)', () => {
    expect(
      refreshFailureKind(new AuthApiError('Invalid Refresh Token', 400, 'refresh_token_not_found'))
    ).toBe('signin')
    expect(refreshFailureKind(new AuthApiError('server error', 500, 'unexpected_failure'))).toBe(
      'signin'
    )
    expect(refreshFailureKind(new AuthUnknownError('not json', {}))).toBe('signin')
    expect(refreshFailureKind(new AuthSessionMissingError())).toBe('signin')
  })

  it('is retry for anything that is not an auth-js error', () => {
    expect(refreshFailureKind(new TypeError('network'))).toBe('retry')
    expect(refreshFailureKind({ status: 400 })).toBe('retry')
    expect(refreshFailureKind(null)).toBe('retry')
  })
})

describe('readErrorBody fallback', () => {
  it('uses the given fallback for a body it cannot read', async () => {
    await expect(
      readErrorBody({ json: async () => Promise.reject(new SyntaxError('html')) }, 'listing failed')
    ).resolves.toEqual({ detail: 'listing failed' })
  })
})
