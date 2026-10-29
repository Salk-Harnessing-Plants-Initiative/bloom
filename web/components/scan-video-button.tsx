"use client";

import { useEffect, useState } from "react";
import { videoErrorMessage, videoResultSummary } from "./scan-video.helpers";
import { useVideoJobPoll } from "./use-video-job-poll";
import {
  POLL_LOST,
  POLL_SIGNED_OUT,
  SCAN_FAILURES,
  countedFrames,
  isActiveJob,
  jobFailureMessage,
  queuedNote,
  renderingNote,
  toVideoJob,
  type VideoJob,
} from "@/lib/video-job";

// `waiting` covers the whole life of a job: queued, then rendering.
type Status = "idle" | "confirming" | "queuing" | "waiting" | "done" | "error";

/** The poll's answer: the stored video's URL, if any, and the latest job. */
function readPoll(body: unknown): { url: string; job: VideoJob | null } {
  const b = body as { download_url?: unknown; job?: unknown } | null;
  const url = typeof b?.download_url === "string" ? b.download_url.trim() : "";
  return { url, job: toVideoJob(b?.job) };
}

export default function ScanVideoButton({
  experimentId,
  scanId,
  initialVideoUrl = null,
  completenessWarning = null,
}: {
  experimentId: number;
  scanId: number;
  // The video already stored for this scan, resolved by the page. Kept in state
  // so a finished job updates it here rather than refetching the whole page.
  initialVideoUrl?: string | null;
  // What the frame viewer says about this scan when it looks partial.
  completenessWarning?: string | null;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [videoUrl, setVideoUrl] = useState<string | null>(initialVideoUrl);
  const [job, setJob] = useState<VideoJob | null>(null);
  const [message, setMessage] = useState<string>("");
  // The job this page is waiting on. The poll asks for it by id, so a newer job
  // on the same scan can neither end the wait nor hide this one's outcome.
  const [expectedJob, setExpectedJob] = useState<string | null>(null);

  const endpoint = `/api/cyl/experiments/${experimentId}/scans/${scanId}/video`;
  // A stored video is keyed by scan alone, so asking how it is going doesn't go
  // through the experiment-scoped generate route.
  const pollEndpoint = `/api/cyl/scans/${scanId}/video`;
  const jobPollUrl = expectedJob
    ? `${pollEndpoint}?${new URLSearchParams({ job_id: expectedJob })}`
    : pollEndpoint;
  const busy = status === "queuing" || status === "waiting";

  // Someone may already have queued this scan; their progress is shown here too.
  useEffect(() => {
    if (initialVideoUrl) return;
    let cancelled = false;
    fetch(pollEndpoint)
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (cancelled || !body) return;
        const { job: current } = readPoll(body);
        if (isActiveJob(current)) {
          setExpectedJob(current.id);
          setJob(current);
          setStatus((s) => (s === "idle" ? "waiting" : s));
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [pollEndpoint, initialVideoUrl]);

  useVideoJobPoll(
    jobPollUrl,
    status === "waiting" && expectedJob !== null,
    (body) => {
      const { url, job: current } = readPoll(body);
      if (!current || current.id !== expectedJob) return;
      setJob(current);
      if (current.status === "failed") {
        setMessage(jobFailureMessage(current, SCAN_FAILURES));
        setStatus("error");
      } else if (current.status === "rendered" || current.status === "kept") {
        if (url) setVideoUrl(url);
        setStatus("done");
      }
    },
    (why) => {
      setMessage(why === "signed_out" ? POLL_SIGNED_OUT : POLL_LOST);
      setStatus("error");
    }
  );

  // A partial scan may be all there will ever be, so this is a confirmation rather than a
  // refusal — but the video it produces is the one everyone sees, and the product offers no
  // way to replace it.
  function requestGenerate() {
    if (busy) return;
    if (completenessWarning && status !== "confirming") {
      setMessage("");
      setStatus("confirming");
      return;
    }
    generate();
  }

  async function generate() {
    if (busy) return;
    setStatus("queuing");
    setMessage("");
    setJob(null);

    let response: Response;
    try {
      response = await fetch(endpoint, { method: "POST" });
    } catch {
      setMessage("Could not reach the video service.");
      setStatus("error");
      return;
    }

    const body = await response.json().catch(() => null);

    // 409 means a video is already stored — show it if it can be found.
    if (response.status === 409) {
      try {
        const res = await fetch(pollEndpoint);
        const { url } = readPoll(res.ok ? await res.json() : null);
        if (url) {
          setVideoUrl(url);
          setStatus("done");
          return;
        }
      } catch {
        // Fall through to the route's own sentence.
      }
    }

    if (!response.ok) {
      setMessage(videoErrorMessage(response.status, body?.detail));
      setStatus("error");
      return;
    }

    if (typeof body?.job_id !== "string") {
      setMessage("The video service returned an unexpected response.");
      setStatus("error");
      return;
    }

    // Created or shared with someone else's click: either way, wait for it.
    setExpectedJob(body.job_id);
    setStatus("waiting");
  }

  const counted = countedFrames(job);
  const waitingNote =
    status === "queuing"
      ? "Queuing…"
      : job?.status === "queued"
        ? queuedNote(job)
        : job?.status === "rendering"
          ? renderingNote(job)
          : "Queued…";

  return (
    <div className="mt-4">
      {/* A stored video is final: replacing one is not offered, so a scan that has one gets
          the link and nothing else. */}
      <div className="flex items-center gap-3">
        {videoUrl ? (
          <a
            href={videoUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-lime-700 underline hover:text-lime-800"
          >
            Open video
          </a>
        ) : status === "confirming" ? null : (
          <button
            type="button"
            onClick={requestGenerate}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-md bg-lime-700 px-4 py-2.5 text-sm font-medium text-stone-50 transition-colors hover:bg-lime-800 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-lime-700"
          >
            {busy ? "Generating video…" : "Generate video"}
          </button>
        )}
      </div>

      {status === "confirming" && (
        <div className="mt-2 max-w-sm rounded-md border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm text-stone-700">
            {completenessWarning} A video made now will be missing those angles, and it
            cannot be replaced later.
          </p>
          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              onClick={generate}
              className="rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-100"
            >
              Generate anyway
            </button>
            <button
              type="button"
              onClick={() => setStatus("idle")}
              className="text-sm text-stone-600 underline hover:text-stone-800"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Fills while frames are countable, pulses while they are not. */}
      {busy && (
        <div className="mt-2">
          <div
            role="progressbar"
            aria-label="Generating video"
            aria-valuenow={counted?.done}
            aria-valuemin={counted ? 0 : undefined}
            aria-valuemax={counted?.total}
            className="h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-stone-200"
          >
            {counted ? (
              <div
                className="h-full bg-lime-600 transition-[width] duration-500"
                style={{ width: `${(counted.done / counted.total) * 100}%` }}
              />
            ) : (
              <div className="h-full w-full animate-pulse bg-lime-600" />
            )}
          </div>
          <p className="mt-2 text-sm text-stone-500 italic">{waitingNote}</p>
        </div>
      )}

      {status === "done" && job && (
        <p className="mt-2 text-sm text-stone-500">{videoResultSummary(job)}</p>
      )}

      {status === "error" && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {message}
        </p>
      )}
    </div>
  );
}
