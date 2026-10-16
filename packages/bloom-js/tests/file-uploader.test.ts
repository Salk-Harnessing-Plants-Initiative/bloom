import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as crypto from 'crypto'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'
import sharp from 'sharp'

import { SupabaseUploader } from '../src/core/supabase/file-uploader'

function fakeStorage(error: unknown = null) {
  const bucket = { upload: vi.fn().mockResolvedValue({ error }) }
  const client = { storage: { from: vi.fn().mockReturnValue(bucket) } }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { uploader: new SupabaseUploader(client as any), client, bucket }
}

let dir: string

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bloom-js-upload-'))
})

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true })
  vi.restoreAllMocks()
})

function writeFile(name: string, bytes: Buffer): string {
  const file = path.join(dir, name)
  fs.writeFileSync(file, bytes)
  return file
}

async function pngFile(name = 'image.png'): Promise<string> {
  const png = await sharp({
    create: { width: 4, height: 4, channels: 3, background: { r: 10, g: 20, b: 30 } },
  })
    .png()
    .toBuffer()
  return writeFile(name, png)
}

describe('uploadRawFile', () => {
  it('uploads the bytes unchanged and returns their SHA-256 and size', async () => {
    const bytes = Buffer.from('II*\u0000 fake tiff bytes')
    const src = writeFile('scan.tif', bytes)
    const { uploader, client, bucket } = fakeStorage()

    const result = await uploader.uploadRawFile(src, 'graviscan/scan.tif', 'graviscan-images')

    expect(result).toEqual({
      error: null,
      hash: crypto.createHash('sha256').update(bytes).digest('hex'),
      sizeBytes: bytes.length,
    })
    expect(client.storage.from).toHaveBeenCalledWith('graviscan-images')
    const [dst, body, options] = bucket.upload.mock.calls[0]
    expect(dst).toBe('graviscan/scan.tif')
    expect(Buffer.compare(body, bytes)).toBe(0)
    expect(options).toEqual({ contentType: 'image/tiff' })
  })

  it.each([
    ['scan.tiff', 'image/tiff'],
    ['image.PNG', 'image/png'],
    ['photo.jpg', 'image/jpeg'],
    ['photo.jpeg', 'image/jpeg'],
    ['data.bin', 'application/octet-stream'],
  ])('infers the content type of %s as %s', async (name, mime) => {
    const src = writeFile(name, Buffer.from('x'))
    const { uploader, bucket } = fakeStorage()
    await uploader.uploadRawFile(src, name, 'b')
    expect(bucket.upload.mock.calls[0][2]).toEqual({ contentType: mime })
  })

  it('uses the content type it is given', async () => {
    const src = writeFile('scan.tif', Buffer.from('x'))
    const { uploader, bucket } = fakeStorage()
    await uploader.uploadRawFile(src, 'scan.tif', 'b', 'application/x-custom')
    expect(bucket.upload.mock.calls[0][2]).toEqual({ contentType: 'application/x-custom' })
  })

  it('refuses a file over the size limit without uploading it', async () => {
    const src = writeFile('huge.tif', Buffer.from('x'))
    const stat = fs.statSync(src)
    vi.spyOn(fs.promises, 'stat').mockResolvedValue({
      ...stat,
      size: 600 * 1024 * 1024,
    } as fs.Stats)
    const { uploader, bucket } = fakeStorage()

    const result = await uploader.uploadRawFile(src, 'huge.tif', 'b')

    expect(result.error?.message).toMatch(/File too large/)
    expect(result.hash).toBeUndefined()
    expect(result.sizeBytes).toBe(600 * 1024 * 1024)
    expect(bucket.upload).not.toHaveBeenCalled()
  })

  it('returns an error for a file that does not exist, without uploading', async () => {
    const { uploader, bucket } = fakeStorage()
    const result = await uploader.uploadRawFile(path.join(dir, 'missing.tif'), 'x', 'b')
    expect(result.error?.message).toMatch(/File error: ENOENT/)
    expect(result.hash).toBeUndefined()
    expect(bucket.upload).not.toHaveBeenCalled()
  })

  it('returns the storage error with the hash and size', async () => {
    const storageError = { message: 'denied', name: 'StorageError' }
    const src = writeFile('scan.tif', Buffer.from('abc'))
    const { uploader } = fakeStorage(storageError)
    const result = await uploader.uploadRawFile(src, 'scan.tif', 'b')
    expect(result.error).toBe(storageError)
    expect(result.sizeBytes).toBe(3)
    expect(result.hash).toHaveLength(64)
  })
})

describe('uploadJpegImage', () => {
  it('re-encodes to JPEG at quality 85 by default', async () => {
    const src = await pngFile()
    const jpeg = vi.spyOn(sharp.prototype, 'jpeg')
    const { uploader, bucket } = fakeStorage()

    expect(await uploader.uploadJpegImage(src, 'out.jpg', 'b')).toEqual({ error: null })

    const [, body, options] = bucket.upload.mock.calls[0]
    expect(options).toEqual({ contentType: 'image/jpeg' })
    expect((await sharp(body).metadata()).format).toBe('jpeg')
    expect(jpeg).toHaveBeenCalledWith({ quality: 85 })
  })

  it('uses the quality it is given', async () => {
    const src = await pngFile()
    const jpeg = vi.spyOn(sharp.prototype, 'jpeg')
    const { uploader } = fakeStorage()
    await uploader.uploadJpegImage(src, 'out.jpg', 'b', { quality: 60 })
    expect(jpeg).toHaveBeenCalledWith({ quality: 60 })
  })

  it('returns the storage error', async () => {
    const storageError = { message: 'denied', name: 'StorageError' }
    const src = await pngFile()
    const { uploader } = fakeStorage(storageError)
    expect(await uploader.uploadJpegImage(src, 'out.jpg', 'b')).toEqual({ error: storageError })
  })
})

describe('uploadImage (unchanged)', () => {
  it('still re-encodes to PNG', async () => {
    const src = await pngFile()
    const { uploader, bucket } = fakeStorage()
    await uploader.uploadImage(src, 'out.png', 'b')
    expect(bucket.upload.mock.calls[0][2]).toEqual({ contentType: 'image/png' })
  })
})
