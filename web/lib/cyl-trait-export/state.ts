/**
 * Process-wide trait-export state (design D1 "State", D2 "How the semaphore behaves").
 *
 * The job registry and the PostgREST semaphore must be one instance per server
 * process, shared by every route module; Next can load a module more than once (per
 * route entry, and on dev reloads), so they live on a `globalThis` symbol, the same
 * pattern Next uses in its own process-error handlers.
 */

import { ExportError } from './errors'
import { PG_CONCURRENCY } from './limits'

type Waiter = { grant: () => void; cancel: () => void }

/**
 * A FIFO counting semaphore for PostgREST calls (tasks.md 10b.1). `run` waits for a
 * slot, and the wait can be cancelled, which leaves the queue. A call that has been
 * issued is never aborted: cancelling the HTTP request would not stop Postgres, so
 * the call runs to completion and its slot frees when it really returns. If the
 * caller was cancelled meanwhile, the result is discarded and the caller gets
 * "cancelled".
 */
export class Semaphore {
  private readonly size: number
  private active = 0
  private readonly waiters: Waiter[] = []

  constructor(size: number) {
    this.size = size
  }

  get inFlight(): number {
    return this.active
  }

  get queued(): number {
    return this.waiters.length
  }

  async run<T>(fn: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
    await this.acquire(signal)
    try {
      const result = await fn(new AbortController().signal)
      if (signal?.aborted) throw cancelled()
      return result
    } catch (e) {
      if (signal?.aborted) throw cancelled()
      throw e
    } finally {
      this.release()
    }
  }

  private acquire(signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) return Promise.reject(cancelled())
    if (this.active < this.size && this.waiters.length === 0) {
      this.active += 1
      return Promise.resolve()
    }
    return new Promise<void>((resolve, reject) => {
      const onAbort = () => waiter.cancel()
      const waiter: Waiter = {
        grant: () => {
          signal?.removeEventListener('abort', onAbort)
          this.active += 1
          resolve()
        },
        cancel: () => {
          const i = this.waiters.indexOf(waiter)
          if (i >= 0) this.waiters.splice(i, 1)
          reject(cancelled())
        },
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      this.waiters.push(waiter)
    })
  }

  private release(): void {
    this.active -= 1
    const next = this.waiters.shift()
    if (next) next.grant()
  }
}

function cancelled(): ExportError {
  return new ExportError('cancelled', 'the export was cancelled')
}

/**
 * Runs `fn` over `items` with at most `limit` outstanding, in order, and returns the
 * results in item order. Combined with the FIFO semaphore this keeps one job or
 * listing from queueing ahead of everyone else: its next call only queues once one of
 * its own calls has finished. After a failure no further item is started.
 */
export async function pool<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length)
  let next = 0
  let failed = false
  const worker = async () => {
    while (!failed && next < items.length) {
      const i = next++
      try {
        results[i] = await fn(items[i])
      } catch (err) {
        failed = true
        throw err
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}

export type ExportState = {
  semaphore: Semaphore
  /** Job registry; the record type lives in jobs.ts. */
  jobs: Map<string, unknown>
  /** Each user's listing in flight: cancel it with `ctrl`; `settled` once it has finished. */
  listings: Map<string, { ctrl: AbortController; settled: Promise<void> }>
  /** The unref'd timer that frees expired jobs; started with the first job. */
  sweeper: ReturnType<typeof setInterval> | null
}

const KEY = Symbol.for('bloom.cylTraitExport')
type Holder = { [KEY]?: ExportState }

function freshState(): ExportState {
  return {
    semaphore: new Semaphore(PG_CONCURRENCY),
    jobs: new Map(),
    listings: new Map(),
    sweeper: null,
  }
}

export function getExportState(): ExportState {
  const holder = globalThis as Holder
  holder[KEY] ??= freshState()
  return holder[KEY]
}

/** Test-only: drop the process state so each test starts clean. */
export function resetExportStateForTests(): void {
  const state = (globalThis as Holder)[KEY]
  if (state?.sweeper) clearInterval(state.sweeper)
  delete (globalThis as Holder)[KEY]
}
