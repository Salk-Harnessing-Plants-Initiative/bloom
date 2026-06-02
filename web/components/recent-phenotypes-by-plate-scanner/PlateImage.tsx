"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";

interface PlateImageProps {
  path: string | null;
  alt?: string;
  className?: string;
  fullClassName?: string;
}

async function getObjectUrl(path: string): Promise<string> {
  const supabase = createClientSupabaseClient();
  const { data } = await supabase.storage
    .from("graviscan-images")
    .createSignedUrl(path, 3600, { transform: { width: 320, quality: 80 } });
  return data?.signedUrl ?? "";
}

async function getFullUrl(path: string): Promise<string> {
  const supabase = createClientSupabaseClient();
  const { data } = await supabase.storage
    .from("graviscan-images")
    .createSignedUrl(path, 3600);
  return data?.signedUrl ?? "";
}

export function PlateImage({
  path,
  alt = "",
  className = "w-[160px] h-[160px]",
  fullClassName,
}: PlateImageProps) {
  const [thumb, setThumb] = useState<string | null>(null);
  const [full, setFull] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!path) return;
    getObjectUrl(path).then(setThumb);
    getFullUrl(path).then(setFull);
  }, [path]);

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
    <a
      href={full ?? thumb}
      target="_blank"
      rel="noopener noreferrer"
      className={`${className} ${fullClassName ?? ""} block overflow-hidden rounded-md border border-stone-200`}
    >
      <img
        src={thumb}
        alt={alt}
        className="h-full w-full object-cover"
        onError={() => setFailed(true)}
      />
    </a>
  );
}
