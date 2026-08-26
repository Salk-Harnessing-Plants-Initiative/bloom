"use client";

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { GRAVISCAN_VIDEOS_BUCKET } from "@/lib/supabase/plate-video-path";

interface PlateVideoProps {
  experimentId: number;
  plateId: string;
  waveNumber: number | null;
  objectPath: string | null;
  // Frames the stored video covers. Null on rows written before the column
  // existed — the service replaces those rather than keeping them, so the
  // button offers Update for them too.
  storedFrames: number | null;
  // Captures that have an image, counted the way the encoder counts them.
  availableFrames: number;
}

type Player =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "ready"; url: string };

type Action = "idle" | "generating" | "pending" | "stalled" | "error";

const POLL_INTERVAL_MS = 10_000;
const POLL_LIMIT_MS = 600_000;

// One sentence per outcome. Which frame failed, or whether the encoder was
// busy, is the service's to log; saying it here is a follow-up.
const FAILED = "Could not generate the video. Try again in a moment.";
const STALLED = "Still encoding. Check back in a few minutes.";

/** A signed URL for a stored video, or `missing` if the object is not there.
 *
 * The HEAD matters: a signed URL is minted for any key, so a row whose object
 * never landed would otherwise render a player that fails on play.
 */
async function resolveVideo(path: string): Promise<Player> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.storage
    .from(GRAVISCAN_VIDEOS_BUCKET)
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

export function PlateVideo({
  experimentId,
  plateId,
  waveNumber,
  objectPath,
  storedFrames,
  availableFrames,
}: PlateVideoProps) {
  const [path, setPath] = useState<string | null>(objectPath);
  const [frames, setFrames] = useState<number | null>(storedFrames);
  const [player, setPlayer] = useState<Player>(
    objectPath ? { status: "loading" } : { status: "missing" },
  );
  const [action, setAction] = useState<Action>("idle");

  const endpoint = `/api/gravi/experiments/${experimentId}/plate-video`;
  const busy = action === "generating" || action === "pending";

  // Server-rendered props win when the page itself changes. They do not change
  // after a generate, so this does not undo one.
  useEffect(() => {
    setPath(objectPath);
    setFrames(storedFrames);
  }, [objectPath, storedFrames]);

  useEffect(() => {
    if (!path) {
      setPlayer({ status: "missing" });
      return;
    }
    let cancelled = false;
    setPlayer({ status: "loading" });
    resolveVideo(path).then((next) => {
      if (!cancelled) setPlayer(next);
    });
    return () => {
      cancelled = true;
    };
  }, [path]);

  // A 504 ends our request but not the encode, so ask whether it has landed
  // rather than offering a second one.
  useEffect(() => {
    if (action !== "pending") return;
    const startedAt = Date.now();
    const query = new URLSearchParams({ plate_id: plateId });
    if (waveNumber !== null) query.set("wave_number", String(waveNumber));

    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${endpoint}?${query}`, {
          signal: AbortSignal.timeout(POLL_INTERVAL_MS),
        });
        if (res.ok) {
          const body = await res.json();
          if (typeof body?.download_url === "string") {
            setPlayer({ status: "ready", url: body.download_url });
            setFrames(availableFrames);
            setAction("idle");
            return;
          }
        }
      } catch {
        // Keep asking — the encode may still be running.
      }
      if (Date.now() - startedAt >= POLL_LIMIT_MS) setAction("stalled");
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [action, endpoint, plateId, waveNumber, availableFrames]);

  // `disabled` is what stops a second click; a `busy` guard here could not,
  // since both handlers of a double-click close over the same render's value.
  // Two requests that do get through are safe anyway: the service holds a
  // per-plate lock and re-plans after taking it, so the second one keeps.
  async function generate() {
    setAction("generating");
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plate_id: plateId, wave_number: waveNumber }),
      });
      if (res.status === 504) {
        setAction("pending");
        return;
      }
      if (!res.ok) {
        setAction("error");
        return;
      }
      const body = await res.json();
      if (typeof body?.object_path !== "string") {
        setAction("error");
        return;
      }
      // Whether it rendered or kept what was there, the service has decided
      // this video is current for the frames that exist.
      setFrames(availableFrames);
      setPath(body.object_path);
      setAction("idle");
    } catch {
      setAction("error");
    }
  }

  const stale = path !== null && (frames === null || frames < availableFrames);
  const newFrames = frames === null ? null : availableFrames - frames;
  const label = path === null ? "Generate" : "Update";
  const note =
    action === "error"
      ? FAILED
      : action === "stalled"
        ? STALLED
        : busy
          ? "Encoding — this can take a few minutes."
          : stale && newFrames
            ? `${newFrames} new ${newFrames === 1 ? "frame" : "frames"} since this was made.`
            : "";

  return (
    <div>
      {player.status === "loading" && (
        <div className="flex h-[60vh] aspect-[5/7] mx-auto animate-pulse items-center justify-center rounded-md border border-stone-200 bg-stone-100 text-sm text-stone-400">
          loading…
        </div>
      )}

      {player.status === "missing" && (
        <div className="flex h-[60vh] aspect-[5/7] mx-auto items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 px-6 text-center text-sm text-stone-400">
          {availableFrames === 0
            ? "No captures with an image for this plate yet."
            : "No time-lapse video for this plate yet."}
        </div>
      )}

      {player.status === "ready" && (
        <video
          key={player.url}
          controls
          preload="metadata"
          className="h-[60vh] aspect-[5/7] mx-auto rounded-md border border-stone-200 bg-black object-cover"
          onError={() => setPlayer({ status: "missing" })}
        >
          <source src={player.url} type="video/mp4" />
        </video>
      )}

      {/* Not offered once the poll gives up: the encode it was waiting on is
          still running upstream, and a second one would race it. */}
      {availableFrames > 0 && action !== "stalled" && (path === null || stale) && (
        <div className="mt-3 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={generate}
            disabled={busy}
            className="rounded-md border border-stone-300 bg-white px-4 py-2 text-sm text-stone-700 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Generating…" : label}
          </button>
        </div>
      )}

      {note && (
        <p className="mt-2 text-center text-xs text-stone-500">{note}</p>
      )}
    </div>
  );
}
