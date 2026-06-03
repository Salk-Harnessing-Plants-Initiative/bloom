"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";

interface PlateVideoProps {
  objectPath: string | null;
}

type State =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "ready"; url: string };

async function resolveVideo(path: string): Promise<State> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.storage
    .from("graviscan-videos")
    .createSignedUrl(path, 3600);
  if (error || !data?.signedUrl) return { status: "missing" };

  try {
    const res = await fetch(data.signedUrl, { method: "HEAD" });
    if (!res.ok) return { status: "missing" };
  } catch {
    return { status: "missing" };
  }
  return { status: "ready", url: data.signedUrl };
}

export function PlateVideo({ objectPath }: PlateVideoProps) {
  const [state, setState] = useState<State>(
    objectPath ? { status: "loading" } : { status: "missing" },
  );

  useEffect(() => {
    if (!objectPath) {
      setState({ status: "missing" });
      return;
    }
    let cancelled = false;
    resolveVideo(objectPath).then((s) => {
      if (!cancelled) setState(s);
    });
    return () => {
      cancelled = true;
    };
  }, [objectPath]);

  if (state.status === "loading") {
    return (
      <div className="flex h-[60vh] aspect-[5/7] mx-auto animate-pulse items-center justify-center rounded-md border border-stone-200 bg-stone-100 text-sm text-stone-400">
        loading…
      </div>
    );
  }

  if (state.status === "missing") {
    return (
      <div className="flex h-[60vh] aspect-[5/7] mx-auto items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 text-sm text-stone-400">
        No time-lapse video available for this plate yet.
      </div>
    );
  }

  return (
    <video
      controls
      preload="metadata"
      className="h-[60vh] aspect-[5/7] mx-auto rounded-md border border-stone-200 bg-black object-cover"
      onError={() => setState({ status: "missing" })}
    >
      <source src={state.url} type="video/mp4" />
    </video>
  );
}
