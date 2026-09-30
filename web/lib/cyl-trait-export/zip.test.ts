/** The export zip (design D2 "Memory and the event loop"; tasks.md 5.3). */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { unzipSync } from 'fflate'
import { describe, expect, it } from 'vitest'

import { ExportError } from './errors'
import { buildZip } from './zip'

const GOLDEN = join(__dirname, '__fixtures__', 'golden')
const bytes = (name: string) => new Uint8Array(readFileSync(join(GOLDEN, name)))

function* slices(data: Uint8Array, size: number): Iterable<Uint8Array> {
  for (let i = 0; i < data.length; i += size) yield data.slice(i, i + size)
}

const concat = (chunks: Uint8Array[]) => Buffer.concat(chunks.map((c) => Buffer.from(c)))

describe('buildZip', () => {
  it('holds exactly the three files with their bytes', async () => {
    const chunks = await buildZip([
      { name: 's.csv', data: slices(bytes('K.csv'), 64) },
      { name: 's.export.json', data: [bytes('K.export.json')] },
      { name: 's.excluded.csv', data: [bytes('K.excluded.csv')] },
    ])
    const files = unzipSync(new Uint8Array(concat(chunks)))
    expect(Object.keys(files)).toEqual(['s.csv', 's.export.json', 's.excluded.csv'])
    expect(Buffer.from(files['s.csv']).equals(Buffer.from(bytes('K.csv')))).toBe(true)
    expect(Buffer.from(files['s.export.json']).equals(Buffer.from(bytes('K.export.json')))).toBe(
      true
    )
    expect(Buffer.from(files['s.excluded.csv']).equals(Buffer.from(bytes('K.excluded.csv')))).toBe(
      true
    )
  })

  it('pulls the CSV lazily and yields to the event loop between slices', async () => {
    let pulled = 0
    let ticks = 0
    const tick = setInterval(() => (ticks += 1), 0)
    function* lazy() {
      for (let i = 0; i < 20; i++) {
        pulled += 1
        yield new TextEncoder().encode(`row ${i}\r\n`)
      }
    }
    try {
      const p = buildZip([{ name: 'a.csv', data: lazy() }])
      expect(pulled).toBeLessThan(20)
      await p
    } finally {
      clearInterval(tick)
    }
    expect(pulled).toBe(20)
    expect(ticks).toBeGreaterThan(0)
  })

  it('rejects when an entry fails mid-stream', async () => {
    function* broken() {
      yield new TextEncoder().encode('ok\r\n')
      throw new Error('source failed')
    }
    await expect(buildZip([{ name: 'a.csv', data: broken() }])).rejects.toThrow('source failed')
  })

  it('stops as cancelled when aborted', async () => {
    const ctrl = new AbortController()
    function* many() {
      for (let i = 0; i < 1000; i++) {
        if (i === 5) ctrl.abort()
        yield new TextEncoder().encode('x\r\n')
      }
    }
    const err = await buildZip([{ name: 'a.csv', data: many() }], ctrl.signal).catch((e) => e)
    expect(err).toBeInstanceOf(ExportError)
    expect((err as ExportError).kind).toBe('cancelled')
  })
})
