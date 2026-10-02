/**
 * A fake ExportDb serving golden/input.json verbatim (design D10). It holds no M2
 * logic: a listing is served only for a recorded batch of scan ids, and coverage and
 * trait rows are the recorded single-call rows filtered to the batch. It can reorder
 * completion, shuffle rows, hold calls, and inject faults.
 */

import input from './golden/input.json'
import type { CsvScan, TraitRow } from '../csv'
import type { ExportDb, DbError, SelectionQuery } from '../db'
import type { RecipeRow } from '../recipes'
import type { CoverageRow, SourceMeta } from '../sidecar'
import type { ScanExtendedRow } from '../metadata'

type Rec = { recipe_key: string; coverage: CoverageRow[]; traits: TraitRow[] }
const recipes = input.recipes as unknown as Record<string, Rec>
const listings: { scanIds: number[]; rows: RecipeRow[] }[] = [
  ...Object.values(input.chunk_listings as unknown as Record<string, { scan_ids: number[]; rows: RecipeRow[] }[]>)
    .flat()
    .map((c) => ({ scanIds: c.scan_ids, rows: c.rows })),
  ...Object.values(input.selection_listings as unknown as Record<string, { scan_ids: number[]; rows: RecipeRow[] }>).map(
    (c) => ({ scanIds: c.scan_ids, rows: c.rows })
  ),
]
const ext = input.scans_extended as unknown as ScanExtendedRow[]
const genotypes = input.genotypes as Record<string, string>
const sources = input.sources as unknown as { id: number; metadata: SourceMeta | null }[]
const labelOf = (key: string) =>
  Object.entries(input.keys as Record<string, string>).find(([, k]) => k === key)?.[0]

export type Call = { method: string; args: unknown[] }
export type FakeOptions = {
  /** Resolve calls in this order: 'issue' (default), 'reverse' or a seeded 'random'. */
  order?: 'issue' | 'reverse' | 'random'
  shuffleRows?: boolean
  /** Hold every call until released (for concurrency tests). */
  hold?: boolean
  /** Return a DbError for the nth call of a method (1-based). */
  failOn?: { method: keyof ExportDb; nth: number; error: DbError }
  /** Mutate a method's result before it is returned. */
  tamper?: Partial<Record<keyof ExportDb, (result: unknown, args: unknown[]) => unknown>>
  experimentVisible?: boolean
}

function seeded(seed: number) {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return s / 0x7fffffff
  }
}

export function fakeDb(opts: FakeOptions = {}) {
  const calls: Call[] = []
  const counts = new Map<string, number>()
  let inFlight = 0
  let peak = 0
  const pending: (() => void)[] = []
  const rand = seeded(42)

  function shuffle<T>(xs: T[]): T[] {
    const out = [...xs]
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1))
      ;[out[i], out[j]] = [out[j], out[i]]
    }
    return out
  }

  async function call<T>(method: keyof ExportDb, args: unknown[], produce: () => T): Promise<T> {
    calls.push({ method, args })
    const n = (counts.get(method) ?? 0) + 1
    counts.set(method, n)
    inFlight += 1
    peak = Math.max(peak, inFlight)
    try {
      if (opts.hold || opts.order === 'reverse' || opts.order === 'random') {
        await new Promise<void>((resolve) => pending.push(resolve))
      }
      if (opts.failOn && opts.failOn.method === method && opts.failOn.nth === n) {
        throw opts.failOn.error
      }
      let result: unknown = produce()
      // Keyset pages come from an ORDER BY scan_id query, so they are never shuffled;
      // every other result is an unordered set as far as the export may assume.
      if (opts.shuffleRows && method !== 'pageScans') {
        if (Array.isArray(result)) result = shuffle(result)
        else if (method === 'traits') {
          const r = result as { rows: unknown[]; count: number }
          result = { ...r, rows: shuffle(r.rows) }
        }
      }
      const t = opts.tamper?.[method]
      return (t ? t(result, args) : result) as T
    } finally {
      inFlight -= 1
    }
  }

  const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i])

  const db: ExportDb = {
    experiment: (id) =>
      call('experiment', [id], () =>
        opts.experimentVisible === false || id !== input.experiment.id
          ? null
          : { id: input.experiment.id, name: input.experiment.name }
      ),
    scanExperiment: (scanId) =>
      call('scanExperiment', [scanId], () => (ext.find((r) => r.scan_id === scanId)?.experiment_id as number) ?? null),
    pageScans: (q: SelectionQuery, after, limit) =>
      call('pageScans', [q, after, limit], () =>
        ext
          .filter((r) => matches(r, q) && (after === null || (r.scan_id as number) > after))
          .slice(0, limit)
      ),
    countScans: (q: SelectionQuery) => call('countScans', [q], () => ext.filter((r) => matches(r, q)).length),
    accessions: (ids) =>
      call('accessions', [ids], () => ids.filter((id) => genotypes[String(id)]).map((id) => ({ id, name: genotypes[String(id)] }))),
    listRecipes: (exp, scanIds) =>
      call('listRecipes', [exp, scanIds], () => {
        const hit = listings.find((l) => same(l.scanIds, scanIds))
        if (!hit) throw new Error(`fake: no recorded listing for ${JSON.stringify(scanIds)}`)
        return hit.rows
      }),
    coverage: (exp, scanIds, key) =>
      call('coverage', [exp, scanIds, key], () => {
        const label = labelOf(key)
        if (!label) throw new Error(`fake: unknown key ${key}`)
        return recipes[label].coverage.filter((c) => scanIds.includes(c.scan_id))
      }),
    traits: (exp, key, scanIds) =>
      call('traits', [exp, key, scanIds], () => {
        const label = labelOf(key)!
        const rows = recipes[label].traits.filter((t) => scanIds.includes(t.scan_id))
        return { rows, count: rows.length }
      }),
    sourceMetadata: (ids) =>
      call('sourceMetadata', [ids], () => sources.filter((s) => ids.includes(s.id)).map((s) => ({ id: s.id, metadata: s.metadata }))),
  }

  /** Release held calls: all at once, in reverse, or in a seeded random order. */
  async function drain() {
    for (let round = 0; round < 1000; round++) {
      await new Promise((r) => setImmediate(r))
      if (pending.length === 0) continue
      const batch = pending.splice(0)
      const ordered = opts.order === 'reverse' ? batch.reverse() : opts.order === 'random' ? shuffle(batch) : batch
      for (const release of ordered) release()
    }
  }

  return {
    db,
    calls,
    get peak() {
      return peak
    },
    get inFlight() {
      return inFlight
    },
    pending,
    drain,
  }
}

function matches(r: ScanExtendedRow, q: SelectionQuery): boolean {
  if ('scanId' in q) return r.scan_id === q.scanId
  if (r.experiment_id !== q.experimentId) return false
  if (q.wave !== undefined && r.wave_number !== q.wave) return false
  if (q.age !== undefined && r.plant_age_days !== q.age) return false
  return true
}

export const RECORDED = { input, recipes, ext }
export type { CsvScan }
