"use client";

import { useCallback, useEffect, useState } from "react";

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
  // A check that failed. Offering Generate here would have a scientist re-render
  // a video that exists.
  | { status: "unknown" }
  | { status: "ready"; url: string };

type Stored = { player: Player; frames: number | null; progress: Progress | null };

type Progress = { stage: string; done: number; total: number };

type Action = "idle" | "generating" | "pending" | "stalled" | "error";

const POLL_INTERVAL_MS = 10_000;
const POLL_LIMIT_MS = 600_000;

// Used when the service sends nothing readable. Its own wording is preferred:
// it knows whether the captures are still uploading, whether another plate is
// encoding, or whether the session has expired.
const FAILED = "Could not generate the video. Try again in a moment.";
const STALLED = "Still encoding. Check back in a few minutes.";
const WORKING = "The video is being made now — please stay on this page.";
const UNCHECKED = "Could not check whether this plate has a video.";

/** What the service says is stored: a playable URL and what it holds.
 *
 * One question, one answer. Storage decides whether the video exists and the
 * route signs the link, so the row is never trusted for presence and a lookup
 * that failed comes back as `unknown` rather than as an absence.
 */
async function fetchStored(
  endpoint: string,
  plateId: string,
  waveNumber: number | null,
  withProgress = false
): Promise<Stored> {
  const query = new URLSearchParams({ plate_id: plateId });
  if (waveNumber !== null) query.set("wave_number", String(waveNumber));
  if (withProgress) query.set("progress", "1");

  try {
    const res = await fetch(`${endpoint}?${query}`);
    if (!res.ok)
      return { player: { status: "unknown" }, frames: null, progress: null };

    const body = await res.json();
    const frames = typeof body?.frames === "number" ? body.frames : null;
    const progress =
      typeof body?.progress?.stage === "string" ? body.progress : null;
    return typeof body?.download_url === "string"
      ? {
          player: { status: "ready", url: body.download_url },
          frames,
          progress: null,
        }
      : { player: { status: "missing" }, frames: null, progress };
  } catch {
    return { player: { status: "unknown" }, frames: null, progress: null };
  }
}

/** The service's own sentence, when it sent one worth reading. */
async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" && body.detail.trim()
      ? body.detail
      : FAILED;
  } catch {
    return FAILED;
  }
}

/** Downloading is ~96% of a render and is countable; encoding is one opaque
 *  ffmpeg call. Falls back when the service reports nothing. */
function progressNote(progress: Progress | null): string {
  if (progress?.stage === "downloading" && progress.total > 0)
    return `Downloading frame ${Math.min(progress.done, progress.total)} of ${progress.total}`;
  if (progress?.stage) return WORKING;
  return "Encoding — this can take a few minutes.";
}

export function PlateVideo({
  experimentId,
  plateId,
  waveNumber,
  objectPath,
  storedFrames,
  availableFrames,
}: PlateVideoProps) {
  const [frames, setFrames] = useState<number | null>(storedFrames);
  // Always loading first: the row is a hint, and a video whose row is missing
  // would otherwise flash "no video" before the route says there is one.
  const [player, setPlayer] = useState<Player>({ status: "loading" });
  const [action, setAction] = useState<Action>("idle");
  const [failure, setFailure] = useState(FAILED);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);

  const endpoint = `/api/gravi/experiments/${experimentId}/plate-video`;
  const busy = action === "generating" || action === "pending";

  const ask = useCallback(
    (withProgress = false) =>
      fetchStored(endpoint, plateId, waveNumber, withProgress),
    [endpoint, plateId, waveNumber]
  );

  // The service is asked once per plate, and again after every generate. The
  // page's own row is a starting hint only — it says what was stored when the
  // page rendered, not whether the object is there now.
  useEffect(() => {
    let cancelled = false;
    setPlayer({ status: "loading" });
    ask().then((next) => {
      if (cancelled) return;
      setPlayer(next.player);
      if (next.player.status === "ready") setFrames(next.frames);
    });
    return () => {
      cancelled = true;
    };
  }, [ask, objectPath, storedFrames]);

  // A 504 ends our request but not the encode, so ask whether it has landed
  // rather than offering a second one.
  useEffect(() => {
    if (action !== "pending") return;
    const startedAt = Date.now();

    const timer = setInterval(async () => {
      const next = await ask(true);
      if (next.player.status === "ready") {
        setPlayer(next.player);
        setFrames(next.frames);
        setAction("idle");
        return;
      }
      // A failed poll says nothing about the render, so it keeps what is on
      // screen. An answered one carrying nothing means the render is over, and
      // a lower count means two polls landed out of order.
      setProgress((seen) =>
        next.player.status === "unknown" ||
        (next.progress && seen && next.progress.done < seen.done)
          ? seen
          : next.progress
      );
      if (Date.now() - startedAt >= POLL_LIMIT_MS) setAction("stalled");
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [action, ask]);

  // `disabled` is what stops a second click; a `busy` guard here could not,
  // since both handlers of a double-click close over the same render's value.
  // Two requests that do get through are safe anyway: the service holds a
  // per-plate lock and re-plans after taking it, so the second one keeps.
  async function generate() {
    setAction("generating");
    setRetryAfter(null);
    setProgress(null);
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
        // The service's own sentence: it knows whether the captures are still
        // uploading, whether another plate is encoding, or whether to sign in.
        setFailure(await detailOf(res));
        const wait = Number(res.headers.get("Retry-After"));
        setRetryAfter(Number.isFinite(wait) && wait > 0 ? wait : null);
        setAction("error");
        return;
      }

      const body = await res.json();
      if (typeof body?.object_path !== "string") {
        setFailure(FAILED);
        setAction("error");
        return;
      }
      // A 200 carries no playable link, so the answer to "can it be watched"
      // comes from the same place it always does.
      setFrames(typeof body.frames === "number" ? body.frames : null);
      const next = await ask();
      setPlayer(next.player);
      if (next.player.status === "ready") setFrames(next.frames);
      setAction("idle");
    } catch {
      setFailure(FAILED);
      setAction("error");
    }
  }

  // The video the service says is there, not the row the page rendered with.
  const held = player.status === "ready";
  const stale = held && (frames === null || frames < availableFrames);
  const newFrames = frames === null ? null : availableFrames - frames;
  const label = held ? "Update" : "Generate";
  const waitHint =
    retryAfter === null ? "" : ` Try again in ${retryAfter} seconds.`;
  const note =
    action === "error"
      ? `${failure}${waitHint}`
      : action === "stalled"
        ? STALLED
        : busy
          ? progressNote(progress)
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

      {(player.status === "missing" || player.status === "unknown") && (
        <div className="flex h-[60vh] aspect-[5/7] mx-auto items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 px-6 text-center text-sm text-stone-400">
          {player.status === "unknown"
            ? UNCHECKED
            : availableFrames === 0
              ? "No captures with an image for this plate yet."
              : "No time-lapse video for this plate yet."}
        </div>
      )}

      {player.status === "ready" && (
        <video
          key={player.url}
          controls
          preload="metadata"
          className="h-[60vh] aspect-[5/7] mx-auto rounded-md border border-stone-200 bg-black object-contain"
          onError={() => setPlayer({ status: "missing" })}
        >
          <source src={player.url} type="video/mp4" />
        </video>
      )}

      {/* Not offered once the poll gives up: the encode it was waiting on is
          still running upstream, and a second one would race it. */}
      {availableFrames > 0 &&
        action !== "stalled" &&
        player.status !== "loading" &&
        player.status !== "unknown" &&
        (!held || stale) && (
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

      {busy && progress?.stage === "downloading" && progress.total > 0 && (
        <div
          role="progressbar"
          aria-label="Generating video"
          aria-valuenow={progress.done}
          aria-valuemin={0}
          aria-valuemax={progress.total}
          className="mx-auto mt-3 h-1.5 w-64 overflow-hidden rounded-full bg-stone-200"
        >
          <div
            className="h-1.5 rounded-full bg-lime-700 transition-[width] duration-500"
            style={{ width: `${Math.min(100, (progress.done / progress.total) * 100)}%` }}
          />
        </div>
      )}

      {note && (
        <p className="mt-2 text-center text-xs text-stone-500">{note}</p>
      )}
    </div>
  );
}
