/**
 * Process-wide export state (design D1 "State", D2 "How the semaphore behaves").
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportError } from './errors'
import { getExportState, pool, resetExportStateForTests, Semaphore } from './state'

function deferred<T = void>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const flush = () => new Promise<void>((r) => setImmediate(r))

describe('Semaphore', () => {
  it('never lets more than its size run at once, across owners', async () => {
    const sem = new Semaphore(3)
    let inFlight = 0
    let peak = 0
    const gates = Array.from({ length: 8 }, () => deferred())
    const runs = gates.map((g) =>
      sem.run(async () => {
        inFlight += 1
        peak = Math.max(peak, inFlight)
        await g.promise
        inFlight -= 1
      })
    )
    await flush()
    expect(inFlight).toBe(3)
    gates.forEach((g) => g.resolve())
    await Promise.all(runs)
    expect(peak).toBe(3)
  })

  it('serves waiters first in, first out', async () => {
    const sem = new Semaphore(1)
    const order: string[] = []
    const first = deferred()
    const a = sem.run(async () => {
      order.push('a')
      await first.promise
    })
    const b = sem.run(async () => void order.push('b'))
    const c = sem.run(async () => void order.push('c'))
    await flush()
    first.resolve()
    await Promise.all([a, b, c])
    expect(order).toEqual(['a', 'b', 'c'])
  })

  it('releases the slot when the call rejects', async () => {
    const sem = new Semaphore(1)
    await expect(sem.run(async () => Promise.reject(new Error('boom')))).rejects.toThrow('boom')
    await expect(sem.run(async () => 'ok')).resolves.toBe('ok')
  })

  it('removes an aborted waiter from the queue', async () => {
    const sem = new Semaphore(1)
    const hold = deferred()
    const running = sem.run(async () => hold.promise)
    const ctrl = new AbortController()
    const waiting = sem.run(async () => 'never', ctrl.signal)
    await flush()
    ctrl.abort()
    await expect(waiting).rejects.toBeInstanceOf(ExportError)
    expect(sem.queued).toBe(0)
    hold.resolve()
    await running
    await expect(sem.run(async () => 'next')).resolves.toBe('next')
  })

  describe('a cancelled caller (tasks.md 10b.1)', () => {
    // Cancelling the HTTP request never stopped Postgres, so an issued call is left to
    // finish: its slot frees when it really returns, with no hold time.
    it('never aborts an issued call; the slot frees when it returns, and the caller gets cancelled', async () => {
      const sem = new Semaphore(1)
      const ctrl = new AbortController()
      const gate = deferred<string>()
      let callSignal: AbortSignal | undefined
      const call = sem.run((signal) => {
        callSignal = signal
        return gate.promise
      }, ctrl.signal)
      await flush()
      ctrl.abort()
      await flush()
      expect(callSignal?.aborted).toBe(false)
      expect(sem.inFlight).toBe(1)
      gate.resolve('late result')
      const err = await call.catch((e) => e)
      expect(err).toBeInstanceOf(ExportError)
      expect((err as ExportError).kind).toBe('cancelled')
      expect(sem.inFlight).toBe(0)
    })
  })
})

describe('pool', () => {
  it('keeps each owner to its limit, so owners interleave', async () => {
    const sem = new Semaphore(3)
    const started: string[] = []
    const gates = new Map<string, { resolve: () => void }>()
    const work = (owner: string) => (i: number) =>
      sem.run(async () => {
        const id = `${owner}${i}`
        started.push(id)
        const g = deferred()
        gates.set(id, g)
        await g.promise
        return id
      })
    const a = pool([1, 2, 3, 4], 3, work('A'))
    await flush()
    const b = pool([1, 2, 3, 4], 3, work('B'))
    await flush()
    expect(started).toEqual(['A1', 'A2', 'A3'])
    gates.get('A1')!.resolve()
    await flush()
    await flush()
    // A4 only queues once A1 is done, so B1 (queued earlier) runs first.
    expect(started).toEqual(['A1', 'A2', 'A3', 'B1'])
    for (const id of ['A2', 'A3']) gates.get(id)!.resolve()
    await flush()
    await flush()
    expect(started.slice(4)).toEqual(['B2', 'B3'])
    const drain = async () => {
      for (let n = 0; n < 20; n++) {
        for (const g of gates.values()) g.resolve()
        await flush()
      }
    }
    await drain()
    await expect(a).resolves.toEqual(['A1', 'A2', 'A3', 'A4'])
    await expect(b).resolves.toEqual(['B1', 'B2', 'B3', 'B4'])
  })

  it('stops starting items after one fails', async () => {
    const seen: number[] = []
    const p = pool([1, 2, 3, 4, 5], 1, async (i) => {
      seen.push(i)
      if (i === 2) throw new Error('stop')
      return i
    })
    await expect(p).rejects.toThrow('stop')
    expect(seen).toEqual([1, 2])
  })
})

describe('getExportState', () => {
  afterEach(() => resetExportStateForTests())

  it('is one instance per process, even across module reloads', async () => {
    const first = getExportState()
    first.jobs.set('probe', { probe: true } as never)
    vi.resetModules()
    const reloaded = await import('./state')
    const second = reloaded.getExportState()
    expect(second).toBe(first)
    expect(second.jobs.get('probe')).toEqual({ probe: true })
    expect(second.semaphore).toBe(first.semaphore)
  })
})
