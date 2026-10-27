/** Polling a job and keeping the latest listing (design D8). */

import { describe, expect, it } from 'vitest'

import {
  FAST_POLL_MS,
  FAST_WINDOW_MS,
  MAX_POLL_FAILURES,
  SLOW_POLL_MS,
  createFailureCounter,
  createLatestGuard,
  phaseLine,
  pollDelay,
} from './poll'

describe('pollDelay', () => {
  it('polls every 2 s up to and including 60 s, then every 5 s', () => {
    expect([FAST_POLL_MS, SLOW_POLL_MS, FAST_WINDOW_MS]).toEqual([2000, 5000, 60_000])
    const table: [number, number][] = [
      [0, 2000],
      [58_000, 2000],
      [60_000, 2000],
      [60_001, 5000],
      [62_000, 5000],
      [120_000, 5000],
    ]
    for (const [elapsed, delay] of table)
      expect([elapsed, pollDelay(elapsed)]).toEqual([elapsed, delay])
  })
})

describe('phaseLine', () => {
  it('says which batch is being read in phase traits', () => {
    expect(phaseLine({ phase: 'traits', done: 3, total: 10 })).toBe('Reading batch 3 of 10')
  })

  it('has a fixed line for every other phase', () => {
    expect(phaseLine({ phase: 'queued', done: 0, total: 0 })).toBe('Waiting to start')
    expect(phaseLine({ phase: 'recipes', done: 1, total: 2 })).toBe('Checking recipes (1 of 2)')
    expect(phaseLine({ phase: 'metadata', done: 1, total: 1 })).toBe('Writing the files')
    expect(phaseLine({ phase: 'done', done: 4, total: 4 })).toBe('Preparing the download')
    expect(phaseLine({ phase: 'something-new', done: 0, total: 0 })).toBe('Working')
  })
})

describe('createLatestGuard', () => {
  it('keeps only the newest listing current', () => {
    const guard = createLatestGuard()
    const first = guard.next()
    expect(guard.isCurrent(first)).toBe(true)
    const second = guard.next()
    expect(guard.isCurrent(first)).toBe(false)
    expect(guard.isCurrent(second)).toBe(true)
  })

  it('keeps separate guards independent', () => {
    const a = createLatestGuard()
    const b = createLatestGuard()
    const n = a.next()
    b.next()
    b.next()
    expect(a.isCurrent(n)).toBe(true)
  })
})

describe('createFailureCounter', () => {
  it('gives up on the third failure in a row, and a success resets the count', () => {
    expect(MAX_POLL_FAILURES).toBe(3)
    const c = createFailureCounter()
    expect([c.fail(), c.fail()]).toEqual([false, false])
    c.ok()
    expect([c.fail(), c.fail(), c.fail()]).toEqual([false, false, true])
  })
})
