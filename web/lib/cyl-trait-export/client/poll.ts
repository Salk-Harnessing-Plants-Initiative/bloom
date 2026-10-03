/**
 * Polling a trait export job, and keeping only the latest listing (design D8).
 * Client-safe.
 */

import type { JobView } from '../jobs'

export const FAST_POLL_MS = 2000
export const SLOW_POLL_MS = 5000
export const FAST_WINDOW_MS = 60_000
export const MAX_POLL_FAILURES = 3

/** Milliseconds until the next poll, `elapsedMs` after the job started or was resumed. */
export function pollDelay(elapsedMs: number): number {
  return elapsedMs <= FAST_WINDOW_MS ? FAST_POLL_MS : SLOW_POLL_MS
}

/** The progress line for a running job (phases from build-export.ts and jobs.ts). */
export function phaseLine(view: Pick<JobView, 'phase' | 'done' | 'total'>): string {
  switch (view.phase) {
    case 'queued':
      return 'Waiting to start'
    case 'recipes':
      return `Checking recipes (${view.done} of ${view.total})`
    case 'traits':
      return `Reading batch ${view.done} of ${view.total}`
    case 'metadata':
      return 'Writing the files'
    case 'done':
      return 'Preparing the download'
    default:
      return 'Working'
  }
}

/** Numbers each listing as it is scheduled; only the latest one is current. */
export function createLatestGuard(): { next: () => number; isCurrent: (n: number) => boolean } {
  let latest = 0
  return {
    next: () => ++latest,
    isCurrent: (n) => n === latest,
  }
}

/** Counts consecutive failed polls; `fail()` is true once polling should stop. */
export function createFailureCounter(): { fail: () => boolean; ok: () => void } {
  let failures = 0
  return {
    fail: () => ++failures >= MAX_POLL_FAILURES,
    ok: () => {
      failures = 0
    },
  }
}
