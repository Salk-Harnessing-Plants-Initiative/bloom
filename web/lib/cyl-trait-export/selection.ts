/**
 * What a trait export selects, and how it is read and recorded (design D2 steps
 * 1-2, D5 `selection`).
 */

import { createHash } from 'node:crypto'

import { ExportError, SELECTION_CHANGED } from './errors'

/** An experiment (optionally one wave and/or one plant age), or one scan. */
export type Selection = { experiment: number; wave?: number; age?: number } | { scan: number }

/** Consecutive chunks of `size`; chunk i (from 1) is "batch i of n". */
export function chunk<T>(ids: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < ids.length; i += size) out.push(ids.slice(i, i + size))
  return out
}

const ascending = (ids: number[]) => [...ids].sort((a, b) => a - b)

/** sha256 (lowercase hex) of the ascending decimal ids joined by `,`, e.g. `3,7,12`. */
export function scanIdsSha256(ids: number[]): string {
  return createHash('sha256').update(ascending(ids).join(','), 'utf8').digest('hex')
}

/** `selection.filters` as recorded in the sidecar; 0 is a real value. */
export function buildFilters(sel: Selection): Record<string, number> {
  if ('scan' in sel) return { scan_id: sel.scan }
  const filters: Record<string, number> = {}
  if (sel.wave !== undefined) filters.wave_number = sel.wave
  if (sel.age !== undefined) filters.plant_age_days = sel.age
  return filters
}

/** `selection.scan_ids`: null for a whole, unfiltered experiment. */
export function selectionScanIds(sel: Selection, ids: number[]): number[] | null {
  if (!('scan' in sel) && sel.wave === undefined && sel.age === undefined) return null
  return ascending(ids)
}

/**
 * Keyset paging by ascending `scan_id`. It stops only on an empty page, so a server
 * row cap can't end it early, and the result must match an exact count of the same
 * query with no repeated id; otherwise the data changed underneath the read.
 */
export async function pageSelection<R extends { scan_id: number }>(
  fetchPage: (after: number | null, limit: number) => Promise<R[]>,
  exactCount: () => Promise<number>,
  pageSize = 1000
): Promise<R[]> {
  const out: R[] = []
  let after: number | null = null
  for (;;) {
    const page = await fetchPage(after, pageSize)
    if (page.length === 0) break
    for (const row of page) {
      if (after !== null && row.scan_id <= after) {
        throw new ExportError('selection_changed', SELECTION_CHANGED)
      }
      out.push(row)
      after = row.scan_id
    }
  }
  if ((await exactCount()) !== out.length) {
    throw new ExportError('selection_changed', SELECTION_CHANGED)
  }
  return out
}
