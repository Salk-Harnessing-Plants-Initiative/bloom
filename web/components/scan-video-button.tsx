"use client";

import { useEffect, useState } from "react";
import {
  isScanVideoResult,
  videoErrorMessage,
  videoResultSummary,
  type ScanVideoResult,
} from "./scan-video.helpers";

// `stalled` is terminal-for-this-page: the encode outlived even the poll, so we
// stop asking without re-offering Generate — a second encode on the same scan
// is the thing this component exists to avoid.
type Status =
  | "idle"
  | "confirming"
  | "generating"
  | "done"
  | "pending"
  | "stalled"
  | "error";

// How often to ask whether a timed-out encode has landed, and how long to keep
// asking before telling the user to come back later.
const POLL_INTERVAL_MS = 10_000;
const POLL_LIMIT_MS = 600_000;

export default function ScanVideoButton({
  experimentId,
  scanId,
  initialVideoUrl = null,
  completenessWarning = null,
}: {
  experimentId: number;
  scanId: number;
  // The video already stored for this scan, resolved by the page. Kept in state
  // so a generate updates it here rather than refetching the whole page.
  initialVideoUrl?: string | null;
  // What the frame viewer says about this scan when it looks partial.
  completenessWarning?: string | null;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [videoUrl, setVideoUrl] = useState<string | null>(initialVideoUrl);
  const [result, setResult] = useState<ScanVideoResult | null>(null);
  const [message, setMessage] = useState<string>("");

  const endpoint = `/api/cyl/experiments/${experimentId}/scans/${scanId}/video`;
  // A stored video is keyed by scan alone, so asking whether one has landed
  // doesn't go through the experiment-scoped generate route.
  const pollEndpoint = `/api/cyl/scans/${scanId}/video`;
  const busy = status === "generating" || status === "pending";

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

  // The stored video's URL, or "" if there isn't one / we couldn't tell.
  async function storedVideoUrl(): Promise<string> {
    try {
      const res = await fetch(pollEndpoint);
      if (!res.ok) return "";
      const body = await res.json();
      return typeof body?.download_url === "string"
        ? body.download_url.trim()
        : "";
    } catch {
      return "";
    }
  }

  async function generate() {
    if (busy) return;
    setStatus("generating");
    setMessage("");

    let response: Response;
    try {
      response = await fetch(endpoint, { method: "POST" });
    } catch {
      setMessage("Could not reach the video service.");
      setStatus("error");
      return;
    }

    const body = await response.json().catch(() => null);

    // 504 means the encode outlived the request, not that it failed — offering
    // "Generate" again here is how you end up with two encodes on one scan.
    // The poll below takes over from here.
    if (response.status === 504) {
      setMessage(
        "Still encoding — this scan is taking longer than the request allows. Waiting for it to finish…"
      );
      setStatus("pending");
      return;
    }

    // 409 means a video landed while this page was open — most likely the
    // encode we started and stopped waiting on. Adopt it, rather than telling
    // the reader to open something the page isn't showing.
    if (response.status === 409) {
      const adopted = await storedVideoUrl();
      if (adopted) {
        setVideoUrl(adopted);
        setStatus("done");
        return;
      }
    }

    if (!response.ok) {
      setMessage(videoErrorMessage(response.status, body?.detail));
      setStatus("error");
      return;
    }

    // The route rejects a success it doesn't recognise; re-check here so a
    // null body can't throw past this point and strand the button as "busy".
    if (!isScanVideoResult(body)) {
      setMessage("The video service returned an unexpected response.");
      setStatus("error");
      return;
    }

    setResult(body);
    setVideoUrl(body.download_url);
    setStatus("done");
  }

  // A 504 ends our request but not the encode — the upstream handler is
  // synchronous, so a client disconnect doesn't cancel it. Nothing else will
  // tell us how it ended, so ask until the video appears.
  useEffect(() => {
    if (status !== "pending") return;

    let active = true;
    let inFlight = false;
    // Wall clock, not a tick count: a slow or hung answer would otherwise stop
    // the budget advancing and the give-up below would never fire.
    const startedAt = Date.now();

    const timer = setInterval(async () => {
      // A slow answer must not stack up behind the next tick.
      if (inFlight) return;
      inFlight = true;

      let response: Response | null = null;
      try {
        // Bounded so a request that never settles can't latch inFlight on and
        // silently end the polling.
        response = await fetch(pollEndpoint, {
          signal: AbortSignal.timeout(POLL_INTERVAL_MS),
        });
      } catch {
        // A blip mid-encode isn't an answer — keep asking.
      } finally {
        inFlight = false;
      }
      if (!active) return;

      if (response?.ok) {
        const body = await response.json().catch(() => null);
        if (!active) return;
        const url =
          typeof body?.download_url === "string" ? body.download_url.trim() : "";
        if (url) {
          setVideoUrl(url);
          setStatus("done");
          return;
        }
      }

      if (Date.now() - startedAt >= POLL_LIMIT_MS) {
        setMessage(
          "This encode is taking longer than expected. It may still finish — reload the page to check."
        );
        // Not "error": the encode is probably still running, and re-offering
        // Generate here would start a second one on the same scan.
        setStatus("stalled");
      }
    }, POLL_INTERVAL_MS);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [status, pollEndpoint]);

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
        ) : status === "stalled" || status === "confirming" ? null : (
          <button
            type="button"
            onClick={requestGenerate}
            disabled={busy}
            className="rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
          >
            {status === "generating"
              ? "Generating video…"
              : status === "pending"
                ? "Still encoding…"
                : "Generate video"}
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

      {/* Encoding a full rotation is slow and runs synchronously upstream — say
          so, rather than leaving a disabled button looking stuck. The bar is
          deliberately indeterminate: the endpoint reports nothing until it
          returns, so any percentage here would be invented. */}
      {busy && (
        <div className="mt-2">
          <div
            role="progressbar"
            aria-label="Generating video"
            className="h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-stone-200"
          >
            <div className="h-full w-full animate-pulse bg-lime-600" />
          </div>
          <p className="mt-2 text-sm text-stone-500 italic">
            {status === "pending"
              ? message
              : "Encoding every frame of this scan. This can take a minute — leave the page open."}
          </p>
        </div>
      )}

      {status === "stalled" && (
        <p className="mt-2 text-sm text-stone-600">{message}</p>
      )}

      {status === "done" && result && (
        <p className="mt-2 text-sm text-stone-500">
          {videoResultSummary(result)}
        </p>
      )}

      {status === "error" && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {message}
        </p>
      )}
    </div>
  );
}
