"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  videoErrorMessage,
  videoResultSummary,
  type ScanVideoResult,
} from "./scan-video.helpers";

type Status = "idle" | "generating" | "done" | "error";

export default function ScanVideoButton({
  experimentId,
  scanId,
  existingVideoUrl = null,
}: {
  experimentId: number;
  scanId: number;
  // The video already stored for this scan, so we don't offer to encode one
  // that exists — a re-encode costs minutes and replaces the stored file.
  existingVideoUrl?: string | null;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<ScanVideoResult | null>(null);
  const [error, setError] = useState<string>("");
  const router = useRouter();

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
    // The viewer's video icon comes from a signed URL resolved when the scan
    // loaded, so without this it keeps showing the pre-generation state.
    router.refresh();
  }

  // Whatever this run produced, otherwise whatever was already stored.
  const videoUrl = result?.download_url ?? existingVideoUrl;

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
            : videoUrl
              ? "Regenerate video"
              : "Generate video"}
        </button>

        {videoUrl && (
          <a
            href={videoUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-lime-700 underline hover:text-lime-800"
          >
            Open video
          </a>
        )}
      </div>

      {videoUrl && status !== "generating" && (
        <p className="mt-2 text-sm text-stone-500 italic">
          Regenerating replaces the stored video for this scan.
        </p>
      )}

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
