import { Database } from './database.types.js'
import { StorageError } from '@supabase/storage-js'

export type Species = Database['public']['Tables']['species']['Row']

export interface FileUploader {
  uploadImage(
    src: string,
    dst: string,
    bucket: string,
    opts?: { pngCompression: number }
  ): Promise<{ error: StorageError | null }>
  uploadJpegImage(
    src: string,
    dst: string,
    bucket: string,
    opts?: { quality: number }
  ): Promise<{ error: StorageError | null }>
  uploadRawFile(
    src: string,
    dst: string,
    bucket: string,
    contentType?: string
  ): Promise<{ error: StorageError | null; hash?: string; sizeBytes?: number }>
}
