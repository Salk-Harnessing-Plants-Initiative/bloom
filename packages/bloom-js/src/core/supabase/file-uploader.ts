// a javascript class that wraps the supabase-js client
// and provides a simple interface for uploading files

import * as fs from "fs";
import sharp from "sharp";

import { Database } from "../../types/database.types";
import { FileUploader } from "../../types/file-uploader";
import { SupabaseClient } from "@supabase/supabase-js";

export type TypedSupabaseClient = SupabaseClient<Database>;

export class SupabaseUploader implements FileUploader {
  supabase: TypedSupabaseClient;

  constructor(supabase: TypedSupabaseClient) {
    this.supabase = supabase;
  }

  async uploadImage(
    src: string,
    dst: string,
    bucket: string,
    opts?: { pngCompression: number }
  ) {
    const pngCompression = opts?.pngCompression || 9;
    const inputBuffer = await fs.promises.readFile(src);

    // Re-encode the image with high compression using sharp
    const compressedBuffer = await sharp(inputBuffer)
      .png({ compressionLevel: pngCompression })
      .toBuffer();

    // const { fileTypeFromBuffer } = await import("file-type");
    // const type = await fileTypeFromBuffer(data);

    const type = { mime: "image/png" };
    const storageOptions = { contentType: type?.mime };
    const { error } = await this.supabase.storage
      .from(bucket)
      .upload(dst, compressedBuffer, storageOptions);

    return { error };
  }
}
