// a javascript class that wraps the supabase-js client
// and provides a simple interface for uploading files

import * as crypto from 'crypto'
import * as fs from 'fs'
import sharp from 'sharp'

import { Database } from '../../types/database.types'
import { FileUploader } from '../../types/file-uploader'
import { SupabaseClient } from '@supabase/supabase-js'
import { StorageError } from '@supabase/storage-js'

export type TypedSupabaseClient = SupabaseClient<Database>

export class SupabaseUploader implements FileUploader {
  supabase: TypedSupabaseClient

  constructor(supabase: TypedSupabaseClient) {
    this.supabase = supabase
  }

  async uploadImage(src: string, dst: string, bucket: string, opts?: { pngCompression: number }) {
    const pngCompression = opts?.pngCompression || 9
    const inputBuffer = await fs.promises.readFile(src)

    // Re-encode the image with high compression using sharp
    const compressedBuffer = await sharp(inputBuffer)
      .png({ compressionLevel: pngCompression })
      .toBuffer()

    // const { fileTypeFromBuffer } = await import("file-type");
    // const type = await fileTypeFromBuffer(data);

    const type = { mime: 'image/png' }
    const storageOptions = { contentType: type?.mime }
    const { error } = await this.supabase.storage
      .from(bucket)
      .upload(dst, compressedBuffer, storageOptions)

    return { error }
  }

  async uploadJpegImage(src: string, dst: string, bucket: string, opts?: { quality: number }) {
    const quality = opts?.quality ?? JPEG_QUALITY_DEFAULT
    const inputBuffer = await fs.promises.readFile(src)
    const compressedBuffer = await sharp(inputBuffer).jpeg({ quality }).toBuffer()

    const { error } = await this.supabase.storage
      .from(bucket)
      .upload(dst, compressedBuffer, { contentType: 'image/jpeg' })
    return { error }
  }

  // Uploads the file exactly as it is (e.g. a GraviScan TIFF), returning its
  // SHA-256 and size so the caller can record them.
  async uploadRawFile(src: string, dst: string, bucket: string, contentType?: string) {
    let buffer: Buffer
    let fileSize: number
    try {
      fileSize = (await fs.promises.stat(src)).size
      if (fileSize > RAW_UPLOAD_MAX_BYTES) {
        return {
          error: storageError(
            `File too large: ${fileSize} bytes (max ${RAW_UPLOAD_MAX_BYTES})`,
            'FileTooLarge',
            '413'
          ),
          hash: undefined,
          sizeBytes: fileSize,
        }
      }
      buffer = await fs.promises.readFile(src)
    } catch (err) {
      const reason =
        err instanceof Error ? (err as NodeJS.ErrnoException).code || err.message : 'Unknown error'
      return {
        error: storageError(`File error: ${reason}`, 'FileError', '400'),
        hash: undefined,
        sizeBytes: undefined,
      }
    }

    const hash = crypto.createHash('sha256').update(new Uint8Array(buffer)).digest('hex')
    const { error } = await this.supabase.storage
      .from(bucket)
      .upload(dst, buffer, { contentType: contentType || inferContentType(src) })
    return { error, hash, sizeBytes: fileSize }
  }
}

const JPEG_QUALITY_DEFAULT = 85
const RAW_UPLOAD_MAX_BYTES = 500 * 1024 * 1024 // 500 MB

function storageError(message: string, name: string, statusCode: string): StorageError {
  return Object.assign(new StorageError(message), { name, statusCode })
}

function inferContentType(filePath: string): string {
  switch (filePath.split('.').pop()?.toLowerCase()) {
    case 'tif':
    case 'tiff':
      return 'image/tiff'
    case 'png':
      return 'image/png'
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg'
    default:
      return 'application/octet-stream'
  }
}
