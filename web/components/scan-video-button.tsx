"use client";

import { useState } from "react";
import {
  videoErrorMessage,
  videoResultSummary,
  type ScanVideoResult,
} from "./scan-video.helpers";

type Status = "idle" | "generating" | "done" | "error";

export default function ScanVideoButton({
  experimentId,
  scanId,
}: {
  experimentId: number;
  scanId: number;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<ScanVideoResult | null>(null);
  const [error, setError] = useState<string>("");

  async function generate() {
    if (status === "generating") return;
    setStatus("generating");
    setError("");

    let response: Response;
    try {
      response = await fetch(
        `/api/cyl/experiments/${experimentId}/scans/${scanId}/video`,
        { method: "POST" }
      );
    } catch {
      setError("Could not reach the video service.");
      setStatus("error");
      return;
    }

    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setError(videoErrorMessage(response.status, body?.detail));
      setStatus("error");
      return;
    }

    setResult(body as ScanVideoResult);
    setStatus("done");
  }

  return (
    <div className="mt-4">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={generate}
          disabled={status === "generating"}
          className="rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
        >
          {status === "generating"
            ? "Generating video…"
            : status === "done"
              ? "Regenerate video"
              : "Generate video"}
        </button>

        {status === "done" && result && (
          <a
            href={result.download_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-lime-700 underline hover:text-lime-800"
          >
            Open video
          </a>
        )}
      </div>

      {/* Encoding a full rotation is slow and runs synchronously upstream — say
          so, rather than leaving a disabled button looking stuck. */}
      {status === "generating" && (
        <p className="mt-2 text-sm text-stone-500 italic">
          Encoding every frame of this scan. This can take a minute — leave the
          page open.
        </p>
      )}

      {status === "done" && result && (
        <p className="mt-2 text-sm text-stone-500">
          {videoResultSummary(result)}
        </p>
      )}

      {status === "error" && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
