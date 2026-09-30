/**
 * Process-wide trait-export state (design D1 "State", D2 "How the semaphore behaves").
 *
 * The job registry and the PostgREST semaphore must be one instance per server
 * process, shared by every route module; Next can load a module more than once (per
 * route entry, and on dev reloads), so they live on a `globalThis` symbol, the same
 * pattern Next uses in its own process-error handlers.
 */

import { ExportError } from './errors'
import { ABORTED_CALL_HOLD_MS, PG_CONCURRENCY } from './limits'

type Waiter = { grant: () => void; cancel: () => void }

/**
 * A FIFO counting semaphore for PostgREST calls. `run` waits for a slot (the wait can
 * be aborted, which leaves the queue), runs the call, and frees the slot when it
 * settles. A call that settles because it was aborted keeps its slot until `holdMs`
 * after it was issued, because PostgREST may still be running its statement.
 */
export class Semaphore {
  private readonly size: number
  private readonly holdMs: number
  private active = 0
  private readonly waiters: Waiter[] = []

  constructor(size: number, holdMs: number) {
    this.size = size
    this.holdMs = holdMs
  }

  get inFlight(): number {
    return this.active
  }

  get queued(): number {
    return this.waiters.length
  }

  async run<T>(fn: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
    await this.acquire(signal)
    const issuedAt = Date.now()
    const callSignal = signal ?? new AbortController().signal
    try {
      return await fn(callSignal)
    } finally {
      if (callSignal.aborted) {
        const wait = Math.max(0, issuedAt + this.holdMs - Date.now())
        setTimeout(() => this.release(), wait)
      } else {
        this.release()
      }
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
  /** The in-flight recipe listing per user, so a newer one can abort the older. */
  listings: Map<string, AbortController>
  /** The unref'd timer that frees expired jobs; started with the first job. */
  sweeper: ReturnType<typeof setInterval> | null
}

const KEY = Symbol.for('bloom.cylTraitExport')
type Holder = { [KEY]?: ExportState }

function freshState(): ExportState {
  return {
    semaphore: new Semaphore(PG_CONCURRENCY, ABORTED_CALL_HOLD_MS),
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
