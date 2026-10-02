/** The dialog's requests and error reading (design D8; spec "Trait download dialog job lifecycle"). */

import { describe, expect, it } from 'vitest'

import {
  GENERIC_ERROR,
  SESSION_TOO_SHORT,
  classifyStartError,
  downloadUrl,
  jobStatusUrl,
  jobUrl,
  readErrorBody,
  recipesUrl,
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
