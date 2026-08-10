"use client";

import { useState } from "react";
import {
  videoErrorMessage,
  videoResultSummary,
  type ScanVideoResult,
} from "./scan-video.helpers";

type Status = "idle" | "generating" | "done" | "pending" | "error";

export default function ScanVideoButton({
  experimentId,
  scanId,
  initialVideoUrl = null,
}: {
  experimentId: number;
  scanId: number;
  // The video already stored for this scan, resolved by the page. Kept in state
  // so a generate updates it here rather than refetching the whole page.
  initialVideoUrl?: string | null;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [videoUrl, setVideoUrl] = useState<string | null>(initialVideoUrl);
  const [result, setResult] = useState<ScanVideoResult | null>(null);
  const [message, setMessage] = useState<string>("");

  async function generate() {
    if (status === "generating") return;
    setStatus("generating");
    setMessage("");

    let response: Response;
    try {
      response = await fetch(
        `/api/cyl/experiments/${experimentId}/scans/${scanId}/video`,
        { method: "POST" }
      );
    } catch {
      setMessage("Could not reach the video service.");
      setStatus("error");
      return;
    }

    const body = await response.json().catch(() => null);

    // 504 means the encode is still running, not that it failed — offering
    // "Generate" again here is how you end up with two encodes on one scan.
    if (response.status === 504) {
      setMessage(videoErrorMessage(response.status, body?.detail));
      setStatus("pending");
      return;
    }

    if (!response.ok) {
      setMessage(videoErrorMessage(response.status, body?.detail));
      setStatus("error");
      return;
    }

    const generated = body as ScanVideoResult;
    setResult(generated);
    setVideoUrl(generated.download_url);
    setStatus("done");
  }

  const busy = status === "generating" || status === "pending";

  return (
    <div className="mt-4">
      {/* A stored video is final — there is no regenerate path. Upstream
          overwrites the scan's video in place and the bucket has no
          versioning, so offering to replace one is offering to destroy it. */}
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
        ) : (
          <button
            type="button"
            onClick={generate}
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

      {/* Encoding a full rotation is slow and runs synchronously upstream — say
          so, rather than leaving a disabled button looking stuck. The bar is
          deliberately indeterminate: the endpoint reports nothing until it
          returns, so any percentage here would be invented. */}
      {status === "generating" && (
        <div className="mt-2">
          <div
            role="progressbar"
            aria-label="Generating video"
            className="h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-stone-200"
          >
            <div className="h-full w-full animate-pulse bg-lime-600" />
          </div>
          <p className="mt-2 text-sm text-stone-500 italic">
            Encoding every frame of this scan. This can take a minute — leave
            the page open.
          </p>
        </div>
      )}

      {status === "pending" && (
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
