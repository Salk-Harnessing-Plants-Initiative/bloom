"use client";

import { cookies } from "next/headers";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
// import { createServerComponentClient } from '@supabase/auth-helpers-nextjs'
import Image from "next/image";
import { Database } from "@/lib/database.types";
import { useEffect, useState } from "react";

async function getObjectUrl(path: string, thumb: boolean) {
  const supabase = createClientComponentClient<Database>();

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
