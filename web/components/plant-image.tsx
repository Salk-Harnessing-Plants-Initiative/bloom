"use client";

// import { cookies } from "next/headers";
import { createClientSupabaseClient } from "@/lib/supabase/client";
// import { createServerSupabaseClient } from '@/lib/supabase/server'
// import Image from "next/image";
import { Database } from "@/lib/database.types";
import { useEffect, useState } from "react";

async function getObjectUrl(path: string, thumb: boolean) {
  const supabase = createClientSupabaseClient();

  const { data, error } = await supabase.storage.from("images").createSignedUrl(
    path,
    120,
    thumb
      ? {
          transform: {
            width: 200,
            quality: 100,
          },
        }
      : {}
  );

  const signedUrl = data?.signedUrl ?? "";
  return signedUrl;
}

export default function PlantImage({
  path,
  thumb,
}: {
  path: string | null;
  thumb: boolean;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    if (path !== null) {
      getObjectUrl(path, thumb).then((url) => {
        setObjectUrl(url);
        setLoading(false);
      });
    }
  }, [path]);

  return (
    <div>
      <div
        className={
          "bg-stone-300 box-content rounded-lg border-4 border-neutral-300" +
          (thumb ? " w-[200px] h-[105px]" : " flex flex-col") +
          (objectUrl === null || loading ? " animate-pulse" : "")
        }
      >
        {objectUrl !== null ? (
          <img src={objectUrl} className="rounded-md" />
        ) : null}
      </div>
    </div>
  );
}
