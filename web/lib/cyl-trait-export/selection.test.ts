/** Selection, batching and paging helpers (design D2 steps 1-2, D5 selection). */

import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'

import { ExportError, SELECTION_CHANGED } from './errors'
import {
  buildFilters,
  chunk,
  pageSelection,
  scanIdsSha256,
  selectionScanIds,
  type Selection,
} from './selection'

describe('chunk', () => {
  it.each([
    [[1, 2, 3], 1, [[1], [2], [3]]],
    [
      [1, 2, 3, 4],
      2,
      [
        [1, 2],
        [3, 4],
      ],
    ],
    [[1, 2, 3, 4, 5], 2, [[1, 2], [3, 4], [5]]],
    [[1, 2, 3], 10, [[1, 2, 3]]],
    [[], 3, []],
  ])('chunk(%j, %i)', (ids, size, out) => {
    expect(chunk(ids, size)).toEqual(out)
  })
})

describe('scanIdsSha256', () => {
  it('hashes the ascending decimal ids joined by commas', () => {
    const want = createHash('sha256').update('3,7,12').digest('hex')
    expect(scanIdsSha256([12, 3, 7])).toBe(want)
  })

  it('sorts numerically, not as strings', () => {
    const want = createHash('sha256').update('9,10,100').digest('hex')
    expect(scanIdsSha256([100, 9, 10])).toBe(want)
  })
})

describe('buildFilters', () => {
  it.each([
    [{ experiment: 1 }, {}],
    [{ experiment: 1, wave: 3 }, { wave_number: 3 }],
    [{ experiment: 1, age: 7 }, { plant_age_days: 7 }],
    [
      { experiment: 1, wave: 3, age: 7 },
      { wave_number: 3, plant_age_days: 7 },
    ],
    [
      { experiment: 1, wave: 0, age: 0 },
      { wave_number: 0, plant_age_days: 0 },
    ],
    [{ scan: 5 }, { scan_id: 5 }],
  ] as [Selection, Record<string, number>][])('buildFilters(%j)', (sel, out) => {
    expect(buildFilters(sel)).toEqual(out)
  })
})

describe('selectionScanIds', () => {
  it('is null only for a whole, unfiltered experiment', () => {
    expect(selectionScanIds({ experiment: 1 }, [3, 1, 2])).toBeNull()
  })

  it('is the sorted ids otherwise', () => {
    expect(selectionScanIds({ experiment: 1, wave: 0 }, [100, 9, 10])).toEqual([9, 10, 100])
    expect(selectionScanIds({ scan: 5 }, [5])).toEqual([5])
  })
})

describe('pageSelection', () => {
  const rows = (ids: number[]) => ids.map((scan_id) => ({ scan_id }))
  const store = rows([1, 2, 3, 4, 5, 6])

  function pager(data: { scan_id: number }[], maxPerPage = Infinity) {
    const calls: (number | null)[] = []
    const fetchPage = async (after: number | null, limit: number) => {
      calls.push(after)
      return data
        .filter((r) => after === null || r.scan_id > after)
        .slice(0, Math.min(limit, maxPerPage))
    }
    return { fetchPage, calls }
  }

  it('pages by keyset until an empty page, each id once', async () => {
    const { fetchPage, calls } = pager(store)
    const got = await pageSelection(fetchPage, async () => 6, 2)
    expect(got.map((r) => r.scan_id)).toEqual([1, 2, 3, 4, 5, 6])
    expect(calls).toEqual([null, 2, 4, 6])
  })

  it('handles an exact multiple and a partial last page alike', async () => {
    const { fetchPage } = pager(rows([1, 2, 3, 4, 5]))
    const got = await pageSelection(fetchPage, async () => 5, 2)
    expect(got.map((r) => r.scan_id)).toEqual([1, 2, 3, 4, 5])
  })

  it('still reads every id when the server returns short pages', async () => {
    const { fetchPage } = pager(store, 1)
    const got = await pageSelection(fetchPage, async () => 6, 1000)
    expect(got.map((r) => r.scan_id)).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('rejects a repeated id', async () => {
    const dup = async (after: number | null) =>
      after === null ? rows([1, 2]) : after === 2 ? rows([2, 3]) : []
    await expect(pageSelection(dup, async () => 3, 2)).rejects.toMatchObject({
      kind: 'selection_changed',
      detail: SELECTION_CHANGED,
    })
  })

  it('rejects a paged set that differs from the exact count', async () => {
    const { fetchPage } = pager(store)
    const p = pageSelection(fetchPage, async () => 7, 2)
    await expect(p).rejects.toBeInstanceOf(ExportError)
    await expect(p).rejects.toMatchObject({ kind: 'selection_changed', detail: SELECTION_CHANGED })
  })
})
