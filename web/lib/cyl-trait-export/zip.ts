/**
 * The export zip (design D2 "Memory and the event loop"). Each entry's slices are
 * pulled one at a time and deflated with fflate's synchronous ZipDeflate, yielding to
 * the event loop between slices so a large export never blocks the server for long.
 * No worker threads, so the bundler cannot break it. Returns the zip as the chunks
 * fflate emitted; the download route streams them back out.
 */

import { Zip, ZipDeflate } from 'fflate'

import { ExportError } from './errors'

export type ZipEntry = { name: string; data: Iterable<Uint8Array> }

const nextTick = () => new Promise<void>((resolve) => setImmediate(resolve))

export async function buildZip(entries: ZipEntry[], signal?: AbortSignal): Promise<Uint8Array[]> {
  const chunks: Uint8Array[] = []
  let failure: Error | null = null
  const zip = new Zip((err, data) => {
    if (err) failure = err
    else chunks.push(data)
  })
  for (const entry of entries) {
    const file = new ZipDeflate(entry.name, { level: 6 })
    zip.add(file)
    for (const slice of entry.data) {
      if (signal?.aborted) {
        zip.terminate()
        throw new ExportError('cancelled', 'the export was cancelled')
      }
      file.push(slice)
      if (failure) throw failure
      await nextTick()
    }
    file.push(new Uint8Array(0), true)
  }
  zip.end()
  if (failure) throw failure
  return chunks
}
