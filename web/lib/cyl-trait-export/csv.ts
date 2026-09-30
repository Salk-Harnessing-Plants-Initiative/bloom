/**
 * The export CSV (design D4). One header row, then one row per included scan in
 * ascending `scan_id`: the 22 metadata cells, `recipe_key`, `source_id`, then the
 * traits present, sorted by Unicode code point. RFC 4180 quoting, CRLF after every
 * line, UTF-8 with no BOM, no comment lines.
 *
 * Values: `cyl_scan_traits.value` is `real`, so every value is a float4 that
 * PostgREST widened to float8 (possibly to 15 digits). A cell is the shortest decimal
 * that parses back to the same float4, so 0.1 stays `0.1`.
 */

import { ExportError } from './errors'
import { METADATA_COLUMNS } from './metadata'

export type TraitRow = {
  scan_id: number
  trait_name: string
  source_id: number | null
  trait_value: number | string | null
}

export type CsvScan = { scanId: number; meta: string[]; sourceId: number | null }

const FIXED_COLUMNS = new Set([...METADATA_COLUMNS, 'recipe_key', 'source_id'])
const NON_FINITE: Record<string, number> = {
  NaN: Number.NaN,
  Infinity: Number.POSITIVE_INFINITY,
  '-Infinity': Number.NEGATIVE_INFINITY,
}

/** The shortest decimal that parses back to the same float4 (e.g. 90, 0.1, 1e-7). */
export function formatFloat4(v: number): string {
  const f = Math.fround(v)
  if (f === 0) return '0'
  for (let p = 1; p <= 9; p++) {
    const n = Number(f.toPrecision(p))
    if (Math.fround(n) === f) return String(n)
  }
  return String(f)
}

/** A cell for one RPC trait value: NULL is empty, non-finite values are literal. */
export function formatTraitValue(v: number | string | null): string {
  if (v === null) return ''
  if (typeof v === 'string') return v
  return formatFloat4(v)
}

/** Unicode code point order (JavaScript's default sort uses UTF-16 code units). */
export function codePointCompare(a: string, b: string): number {
  const x = [...a]
  const y = [...b]
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!
    if (d !== 0) return d
  }
  return x.length - y.length
}

function quote(cell: string): string {
  return /[",\r\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell
}

/** One CSV line with CRLF. Cells are written as-is apart from RFC 4180 quoting. */
export function csvLine(cells: string[]): string {
  return cells.map(quote).join(',') + '\r\n'
}

type ScanTraits = { idx: Uint32Array; values: Float64Array; nulls: Uint8Array }

function toValue(v: unknown): { value: number; isNull: boolean } | undefined {
  if (v === null) return { value: 0, isNull: true }
  if (typeof v === 'number') return { value: Math.fround(v), isNull: false }
  if (typeof v === 'string' && v in NON_FINITE) return { value: NON_FINITE[v], isNull: false }
  return undefined
}

function valueCell(value: number, isNull: boolean): string {
  if (isNull) return ''
  if (Number.isNaN(value)) return 'NaN'
  if (value === Number.POSITIVE_INFINITY) return 'Infinity'
  if (value === Number.NEGATIVE_INFINITY) return '-Infinity'
  return formatFloat4(value)
}

/**
 * Long trait rows pivoted to one row per scan, held in typed arrays (about 13 bytes
 * per value) so a large experiment stays off the V8 heap. Each `add` call is sealed at
 * once; it rejects a repeated (scan, trait), a trait named like a fixed column, and a
 * value that is not a number, NULL, NaN or an infinity (design D2 step 6 e, g, h).
 */
export class TraitPivot {
  private readonly nameIndex = new Map<string, number>()
  private readonly names: string[] = []
  private readonly scans = new Map<number, ScanTraits>()
  private sortedCache: string[] | null = null
  private columnCache: Map<number, number> | null = null

  get traitNames(): string[] {
    this.sortedCache ??= [...this.names].sort(codePointCompare)
    return this.sortedCache
  }

  hasScan(scanId: number): boolean {
    return this.scans.has(scanId)
  }

  add(rows: TraitRow[]): void {
    const grouped = new Map<number, { idx: number[]; values: number[]; nulls: number[] }>()
    for (const row of rows) {
      if (FIXED_COLUMNS.has(row.trait_name)) {
        throw new ExportError(
          'integrity',
          `a trait is named like a fixed column: "${row.trait_name}"`
        )
      }
      const parsed = toValue(row.trait_value)
      if (parsed === undefined) {
        throw new ExportError(
          'integrity',
          `scan ${row.scan_id} has a trait value that is not a number`
        )
      }
      let i = this.nameIndex.get(row.trait_name)
      if (i === undefined) {
        i = this.names.length
        this.names.push(row.trait_name)
        this.nameIndex.set(row.trait_name, i)
        this.sortedCache = null
        this.columnCache = null
      }
      let g = grouped.get(row.scan_id)
      if (g === undefined) {
        g = { idx: [], values: [], nulls: [] }
        grouped.set(row.scan_id, g)
      }
      g.idx.push(i)
      g.values.push(parsed.value)
      g.nulls.push(parsed.isNull ? 1 : 0)
    }
    for (const [scanId, g] of grouped) {
      const prev = this.scans.get(scanId)
      const idx = prev ? [...prev.idx, ...g.idx] : g.idx
      const sorted = [...idx].sort((a, b) => a - b)
      for (let k = 1; k < sorted.length; k++) {
        if (sorted[k] === sorted[k - 1]) {
          throw new ExportError(
            'integrity',
            `scan ${scanId} has trait "${this.names[sorted[k]]}" more than once`
          )
        }
      }
      this.scans.set(scanId, {
        idx: Uint32Array.from(idx),
        values: Float64Array.from(prev ? [...prev.values, ...g.values] : g.values),
        nulls: Uint8Array.from(prev ? [...prev.nulls, ...g.nulls] : g.nulls),
      })
    }
  }

  /** The trait cells of one scan, in `traitNames` order; absent traits are empty. */
  rowCells(scanId: number): string[] {
    const order = this.traitNames
    this.columnCache ??= new Map(order.map((name, c) => [this.nameIndex.get(name)!, c]))
    const column = this.columnCache
    const cells: string[] = new Array(order.length).fill('')
    const s = this.scans.get(scanId)
    if (s) {
      for (let k = 0; k < s.idx.length; k++) {
        cells[column.get(s.idx[k])!] = valueCell(s.values[k], s.nulls[k] === 1)
      }
    }
    return cells
  }
}

/** Header, then one row per scan in ascending `scan_id`. */
export function* csvRows(
  pivot: TraitPivot,
  scans: CsvScan[],
  recipeKey: string
): Iterable<string[]> {
  yield [...METADATA_COLUMNS, 'recipe_key', 'source_id', ...pivot.traitNames]
  for (const s of [...scans].sort((a, b) => a.scanId - b.scanId)) {
    yield [
      ...s.meta,
      recipeKey,
      s.sourceId === null ? '' : String(s.sourceId),
      ...pivot.rowCells(s.scanId),
    ]
  }
}

/**
 * The CSV as UTF-8 slices of about `cellsPerSlice` cells, each a fresh buffer (they
 * are handed to the zip deflater and not reused). A row is never split.
 */
export function* csvSlices(rows: Iterable<string[]>, cellsPerSlice: number): Iterable<Uint8Array> {
  const encoder = new TextEncoder()
  let text = ''
  let cells = 0
  for (const row of rows) {
    if (cells > 0 && cells + row.length > cellsPerSlice) {
      yield encoder.encode(text)
      text = ''
      cells = 0
    }
    text += csvLine(row)
    cells += row.length
  }
  if (text) yield encoder.encode(text)
}
