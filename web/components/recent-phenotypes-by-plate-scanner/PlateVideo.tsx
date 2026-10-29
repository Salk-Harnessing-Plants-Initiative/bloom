"use client";

import { useCallback, useEffect, useState } from "react";
import { useVideoJobPoll } from "@/components/use-video-job-poll";
import {
  PLATE_FAILURES,
  POLL_LOST,
  POLL_SIGNED_OUT,
  countedFrames,
  isActiveJob,
  jobFailureMessage,
  queuedNote,
  renderingNote,
  toVideoJob,
  type VideoJob,
} from "@/lib/video-job";

interface PlateVideoProps {
  experimentId: number;
  plateId: string;
  waveNumber: number | null;
  objectPath: string | null;
  // Frames the stored video covers, as the page rendered. A starting hint only:
  // the route's answer replaces it.
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
  // Stored, but the signed link would not play. Still a video, so not "missing".
  | { status: "broken" }
  | { status: "ready"; url: string };

type Stored = { player: Player; frames: number | null; job: VideoJob | null };

type Action = "idle" | "queuing" | "waiting" | "error";

// Used when the service sends nothing readable. Its own wording is preferred:
// it knows whether the request was malformed or the session has expired.
const FAILED = "Could not queue the video. Try again in a moment.";
const UNCHECKED = "Could not check whether this plate has a video.";
const UNPLAYABLE = "The video could not be loaded. Reload the page to try again.";

/** The stored video and the plate's latest job, from one answer.
 *
 * Storage decides whether the video exists and the route signs the link, so the
 * row is never trusted for presence and a lookup that failed comes back as
 * `unknown` rather than as an absence.
 */
function readStored(body: unknown): Stored {
  const b = body as { download_url?: unknown; frames?: unknown; job?: unknown } | null;
  const job = toVideoJob(b?.job);
  return typeof b?.download_url === "string"
    ? {
        player: { status: "ready", url: b.download_url },
        frames: typeof b.frames === "number" ? b.frames : null,
        job,
      }
    : { player: { status: "missing" }, frames: null, job };
}

async function fetchStored(url: string): Promise<Stored> {
  try {
    const res = await fetch(url);
    if (!res.ok) return { player: { status: "unknown" }, frames: null, job: null };
    return readStored(await res.json());
  } catch {
    return { player: { status: "unknown" }, frames: null, job: null };
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
  const [job, setJob] = useState<VideoJob | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  // The job this page is waiting on. The poll asks for it by id, so a newer job
  // on the same plate can neither end the wait nor hide this one's outcome.
  const [expectedJob, setExpectedJob] = useState<string | null>(null);

  const endpoint = `/api/gravi/experiments/${experimentId}/plate-video`;
  const query = new URLSearchParams({ plate_id: plateId });
  if (waveNumber !== null) query.set("wave_number", String(waveNumber));
  const pollUrl = `${endpoint}?${query}`;
  const jobPollUrl = expectedJob
    ? `${pollUrl}&${new URLSearchParams({ job_id: expectedJob })}`
    : pollUrl;
  const busy = action === "queuing" || action === "waiting";

  const show = useCallback((next: Stored) => {
    setPlayer(next.player);
    setFrames(next.player.status === "ready" ? next.frames : null);
  }, []);

  // Asked once per plate. Someone else's running job is shown here too.
  useEffect(() => {
    let cancelled = false;
    setPlayer({ status: "loading" });
    fetchStored(pollUrl).then((next) => {
      if (cancelled) return;
      if (next.player.status !== "unknown") show(next);
      else setPlayer(next.player);
      if (isActiveJob(next.job)) {
        setExpectedJob(next.job.id);
        setJob(next.job);
        setAction((a) => (a === "idle" ? "waiting" : a));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pollUrl, objectPath, storedFrames, show]);

  useVideoJobPoll(
    jobPollUrl,
    action === "waiting" && expectedJob !== null,
    (body) => {
      const next = readStored(body);
      const current = next.job;
      if (!current || current.id !== expectedJob) return;
      setJob(current);
      if (current.status === "failed") {
        setFailure(jobFailureMessage(current, PLATE_FAILURES));
        setAction("error");
      } else if (current.status === "rendered" || current.status === "kept") {
        show(next);
        setAction("idle");
      }
    },
    (why) => {
      setFailure(why === "signed_out" ? POLL_SIGNED_OUT : POLL_LOST);
      setAction("error");
    }
  );

  // `disabled` is what stops a second click; two requests that do get through
  // share one job anyway, since the database allows one active job per plate.
  async function generate() {
    setAction("queuing");
    setRetryAfter(null);
    setJob(null);
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plate_id: plateId, wave_number: waveNumber }),
      });
      if (!res.ok) {
        setFailure(await detailOf(res));
        const wait = Number(res.headers.get("Retry-After"));
        setRetryAfter(Number.isFinite(wait) && wait > 0 ? wait : null);
        setAction("error");
        return;
      }
      const body = await res.json();
      if (typeof body?.job_id !== "string") {
        setFailure(FAILED);
        setAction("error");
        return;
      }
      // Created or shared with someone else's click: either way, wait for it.
      setExpectedJob(body.job_id);
      setAction("waiting");
    } catch {
      setFailure(FAILED);
      setAction("error");
    }
  }

  // The video the route says is stored, not the row the page rendered with.
  const held = player.status === "ready" || player.status === "broken";
  const stale = held && (frames === null || frames < availableFrames);
  const newFrames = frames === null ? null : availableFrames - frames;
  const label = held ? "Update" : "Generate";
  const waitHint =
    retryAfter === null ? "" : ` Try again in ${retryAfter} seconds.`;
  const counted = countedFrames(job);
  const note =
    action === "error"
      ? `${failure}${waitHint}`
      : action === "queuing"
        ? "Queuing…"
        : action === "waiting"
          ? job?.status === "rendering"
            ? renderingNote(job)
            : job
              ? queuedNote(job)
              : "Queued…"
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

      {(player.status === "missing" ||
        player.status === "unknown" ||
        player.status === "broken") && (
        <div className="flex h-[60vh] aspect-[5/7] mx-auto items-center justify-center rounded-md border border-dashed border-stone-300 bg-stone-50 px-6 text-center text-sm text-stone-400">
          {player.status === "unknown"
            ? UNCHECKED
            : player.status === "broken"
              ? UNPLAYABLE
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
          onError={() => setPlayer({ status: "broken" })}
        >
          <source src={player.url} type="video/mp4" />
        </video>
      )}

      {availableFrames > 0 &&
        player.status !== "loading" &&
        player.status !== "unknown" &&
        player.status !== "broken" &&
        (!held || stale || busy) && (
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

      {/* Fills while the frames are countable and sweeps while they are not. */}
      {busy && (
        <div
          role="progressbar"
          aria-label="Generating video"
          aria-valuenow={counted?.done}
          aria-valuemin={counted ? 0 : undefined}
          aria-valuemax={counted?.total}
          className="mx-auto mt-3 h-1.5 w-64 overflow-hidden rounded-full bg-stone-200"
        >
          {counted ? (
            <div
              className="h-1.5 rounded-full bg-lime-700 transition-[width] duration-500"
              style={{ width: `${(counted.done / counted.total) * 100}%` }}
            />
          ) : (
            <div className="h-1.5 w-1/4 animate-sweep rounded-full bg-lime-700" />
          )}
        </div>
      )}

      {note && (
        <p
          role={action === "error" ? "alert" : undefined}
          className="mt-2 text-center text-xs text-stone-500"
        >
          {note}
        </p>
      )}
    </div>
  );
}
