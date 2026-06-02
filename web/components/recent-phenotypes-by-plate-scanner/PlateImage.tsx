"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";

interface PlateImageProps {
  path: string | null;
  alt?: string;
  className?: string;
}

// Scanner uploads are TIFF (lossless archive). Browsers can't render TIFF
// natively, so both views go through Supabase's image transformer (which
// shells out to imgproxy / libvips in this stack) to land on a JPEG.
type TransformOpts = {
  width?: number;
  height?: number;
  quality?: number;
  format?: "origin" | "jpg" | "png" | "webp";
};

async function getThumbUrl(path: string): Promise<string> {
  const supabase = createClientSupabaseClient();
  const { data } = await supabase.storage
    .from("graviscan-images")
    .createSignedUrl(path, 3600, {
      transform: { width: 480, quality: 80, format: "jpg" } as TransformOpts as {
        width: number;
        quality: number;
      },
    });
  return data?.signedUrl ?? "";
}

async function getFullUrl(path: string): Promise<string> {
  const supabase = createClientSupabaseClient();
  const { data } = await supabase.storage
    .from("graviscan-images")
    .createSignedUrl(path, 3600, {
      transform: { width: 2400, quality: 85, format: "jpg" } as TransformOpts as {
        width: number;
        quality: number;
      },
    });
  return data?.signedUrl ?? "";
}

export function PlateImage({
  path,
  alt = "",
  className = "w-[160px] h-[160px]",
}: PlateImageProps) {
  const [thumb, setThumb] = useState<string | null>(null);
  const [full, setFull] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!path) return;
    getThumbUrl(path).then(setThumb);
    getFullUrl(path).then(setFull);
  }, [path]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!path) {
    return (
      <div
        className={`${className} flex items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 text-xs text-stone-400`}
      >
        no image
      </div>
    );
  }

  if (failed || !thumb) {
    return (
      <div
        className={`${className} flex items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 text-xs text-stone-400`}
      >
        {failed ? "missing" : "loading…"}
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`${className} block overflow-hidden rounded-md border border-stone-200 transition hover:border-lime-700`}
        aria-label={`Open ${alt || "image"} full view`}
      >
        <img
          src={thumb}
          alt={alt}
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6"
          onClick={() => setOpen(false)}
        >
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
            }}
            className="absolute top-4 right-4 rounded-md bg-white/10 px-3 py-1 text-sm text-white hover:bg-white/20"
            aria-label="Close"
          >
            Close ✕
          </button>
          <img
            src={full ?? thumb}
            alt={alt}
            className="max-h-full max-w-full rounded-md"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}
