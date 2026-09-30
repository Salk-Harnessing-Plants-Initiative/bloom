/**
 * The export CSV (design D4; spec "Trait export file conventions"). Golden files are
 * hand-written; input.json holds the RPC rows as PostgreSQL rendered them.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import input from './__fixtures__/golden/input.json'
import {
  codePointCompare,
  csvLine,
  csvRows,
  csvSlices,
  formatFloat4,
  formatTraitValue,
  TraitPivot,
  type TraitRow,
} from './csv'
import { ExportError } from './errors'
import { METADATA_COLUMNS, metadataCells, type ScanExtendedRow } from './metadata'

const GOLDEN = join(__dirname, '__fixtures__', 'golden')
const golden = (name: string) => readFileSync(join(GOLDEN, name))

const f32 = (x: number) => Math.fround(x)

describe('formatFloat4', () => {
  it.each([
    [0.100000001490116, '0.1'],
    [0.10000000149011612, '0.1'],
    [3.40282346638529e38, '3.4028235e+38'],
    [3.4028234663852886e38, '3.4028235e+38'],
    [1.0000000116861e-7, '1e-7'],
    [12.7, '12.7'],
    [90, '90'],
    [4, '4'],
    [85.25, '85.25'],
    [10.125, '10.125'],
    [-2.5, '-2.5'],
    [-0, '0'],
    [0, '0'],
  ])('formatFloat4(%s) is %s', (v, out) => {
    expect(formatFloat4(v)).toBe(out)
  })

  it('always parses back to the same float4, with no shorter form doing so', () => {
    for (const v of [0.1, 1 / 3, 123456.789, 1e-30, 6.02e23, 2 ** -126, 16777217]) {
      const s = formatFloat4(v)
      expect(f32(Number(s))).toBe(f32(v))
      const digits = s.replace(/^-/, '').replace(/e.*$/, '').replace('.', '').replace(/^0+/, '')
      if (digits.length > 1) {
        const shorter = f32(v).toPrecision(digits.length - 1)
        expect(f32(Number(shorter))).not.toBe(f32(v))
      }
    }
  })
})

describe('formatTraitValue', () => {
  it.each([
    [null, ''],
    ['NaN', 'NaN'],
    ['Infinity', 'Infinity'],
    ['-Infinity', '-Infinity'],
    [0.100000001490116, '0.1'],
  ] as const)('formatTraitValue(%j) is %j', (v, out) => {
    expect(formatTraitValue(v)).toBe(out)
  })
})

describe('codePointCompare', () => {
  it('orders by code point, not UTF-16 unit or locale', () => {
    const names = ['𝛼_angle', '～tilde', 'Ångström', 'curve', 'Zeta']
    expect([...names].sort(codePointCompare)).toEqual([
      'Zeta',
      'curve',
      'Ångström',
      '～tilde',
      '𝛼_angle',
    ])
    // JavaScript's default sort puts the astral 𝛼 (a D835 surrogate) before U+FF5E.
    expect([...names].sort().indexOf('𝛼_angle')).toBeLessThan([...names].sort().indexOf('～tilde'))
  })
})

describe('csvLine', () => {
  it.each([
    [['a', 'b'], 'a,b\r\n'],
    [['a,b', 'c'], '"a,b",c\r\n'],
    [['say "hi"'], '"say ""hi"""\r\n'],
    [['line\nbreak'], '"line\nbreak"\r\n'],
    [['cr\rhere'], '"cr\rhere"\r\n'],
    [['plain space ', ''], 'plain space ,\r\n'],
    [['=1+1', '-2', '+3', '@x'], '=1+1,-2,+3,@x\r\n'],
  ])('csvLine(%j)', (cells, out) => {
    expect(csvLine(cells)).toBe(out)
  })
})

type Rec = {
  traits: TraitRow[]
  coverage: { scan_id: number; status: string; source_id: number | null }[]
  recipe_key: string
}
const recipes = input.recipes as unknown as Record<string, Rec>
const ext = new Map(
  (input.scans_extended as unknown as ScanExtendedRow[]).map((r) => [r.scan_id as number, r])
)
const genotypes = input.genotypes as Record<string, string>

function goldenCsv(label: string, only?: number[]) {
  const rec = recipes[label]
  const pivot = new TraitPivot()
  const included = rec.coverage.filter(
    (c) => c.status === 'included' && (!only || only.includes(c.scan_id))
  )
  pivot.add(rec.traits.filter((t) => included.some((c) => c.scan_id === t.scan_id)))
  const scans = included.map((c) => {
    const row = ext.get(c.scan_id)!
    return {
      scanId: c.scan_id,
      meta: metadataCells(row, genotypes[String(row.accession_id)] ?? null),
      sourceId: c.source_id,
    }
  })
  return { pivot, rows: csvRows(pivot, scans, rec.recipe_key) }
}

const joinSlices = (slices: Iterable<Uint8Array>) =>
  Buffer.concat([...slices].map((s) => Buffer.from(s)))

describe('csvRows and csvSlices', () => {
  it.each([
    ['K', 'K.csv'],
    ['legacy:9', 'legacy-9.csv'],
    ['unattributed', 'unattributed.csv'],
  ])('reproduce the golden %s CSV byte for byte', (label, file) => {
    const { rows } = goldenCsv(label)
    expect(joinSlices(csvSlices(rows, 50_000)).equals(golden(file))).toBe(true)
  })

  it('gives the same bytes whatever the slice size', () => {
    const one = joinSlices(csvSlices(goldenCsv('K').rows, 1))
    expect(one.equals(golden('K.csv'))).toBe(true)
  })

  it('emits fresh slices of at most the requested cells, with no BOM', () => {
    const slices = [...csvSlices(goldenCsv('K').rows, 40)]
    expect(slices.length).toBeGreaterThan(1)
    expect(new Set(slices.map((s) => s.buffer)).size).toBe(slices.length)
    expect([...slices[0].slice(0, 3)]).not.toEqual([0xef, 0xbb, 0xbf])
  })

  it('writes the header as metadata, recipe_key, source_id, then code-point-sorted traits', () => {
    const { pivot } = goldenCsv('K')
    expect(pivot.traitNames).toEqual([
      'Zeta_depth',
      'curve_index_median',
      'lateral_count_min',
      'lateral_length_mean',
      'primary_length',
      'Ångström_width',
      '～tilde_score',
      '𝛼_angle',
    ])
    const text = golden('K.csv').toString('utf8')
    expect(text.split('\r\n')[0]).toBe(
      [...METADATA_COLUMNS, 'recipe_key', 'source_id', ...pivot.traitNames].join(',')
    )
    expect(text.split('\n').some((l) => l.startsWith('#'))).toBe(false)
    expect(text.endsWith('\r\n')).toBe(true)
  })

  it("gives a scan export only that scan's traits", () => {
    const { pivot } = goldenCsv('K', [100])
    expect(pivot.traitNames).not.toContain('curve_index_median')
    expect(pivot.traitNames).not.toContain('～tilde_score')
  })
})

describe('TraitPivot integrity', () => {
  const row = (over: Partial<TraitRow>): TraitRow => ({
    scan_id: 1,
    trait_name: 't',
    source_id: 5,
    trait_value: 1,
    ...over,
  })

  it('rejects a repeated scan and trait pair', () => {
    const p = new TraitPivot()
    expect(() => p.add([row({}), row({ trait_value: 2 })])).toThrow(ExportError)
  })

  it('rejects a trait named like a fixed column', () => {
    expect(() => new TraitPivot().add([row({ trait_name: 'genotype' })])).toThrow(ExportError)
    expect(() => new TraitPivot().add([row({ trait_name: 'source_id' })])).toThrow(ExportError)
  })

  it('rejects a value that is not a number, null, NaN or an infinity', () => {
    expect(() => new TraitPivot().add([row({ trait_value: 'abc' as never })])).toThrow(ExportError)
    expect(() => new TraitPivot().add([row({ trait_value: true as never })])).toThrow(ExportError)
  })

  it('keeps NULL apart from NaN', () => {
    const p = new TraitPivot()
    p.add([
      row({ trait_name: 'a', trait_value: null }),
      row({ trait_name: 'b', trait_value: 'NaN' }),
    ])
    const [line] = [
      ...csvRows(p, [{ scanId: 1, meta: Array(22).fill(''), sourceId: 5 }], 'k'),
    ].slice(1)
    expect(line.slice(-2)).toEqual(['', 'NaN'])
  })
})
