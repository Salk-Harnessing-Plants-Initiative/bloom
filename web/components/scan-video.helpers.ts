// Pure helpers for on-demand cyl scan video generation, split out so the
// user-facing messaging is unit-testable without a network round-trip or a
// rendered component.

import type { VideoJob } from "@/lib/video-job";

// What to tell the user when generation fails. The upstream detail is specific
// ("No images found for scan 5") and safe to surface, so prefer it; fall back to
// the status when the failure came from the proxy itself and has no body.
export function videoErrorMessage(
  status: number,
  detail?: string | null
): string {
  const trimmed = typeof detail === "string" ? detail.trim() : "";
  if (trimmed) return trimmed;

  if (status === 401) return "Sign in to generate a video.";
  if (status === 403) return "You do not have access to this scan.";
  if (status === 404) return "This scan was not found in this experiment.";
  if (status === 429)
    return "Too many video requests. Wait a minute and try again.";
  // The route words its own 503s; one with no detail is the service unable to queue the job.
  if (status === 503)
    return "The video could not be queued right now, so nothing was changed. Try again shortly.";
  if (status >= 500) return "Video generation failed. Try again in a moment.";
  return `Could not generate the video (HTTP ${status}).`;
}

// One line describing what a finished job produced. An unreadable frame is
// skipped, not fatal, so "done" on its own would overstate the result.
export function videoResultSummary(job: VideoJob): string {
  // Nothing was written, so there is no frame count to report.
  if (job.status === "kept") {
    return "This scan already has a video — the stored one was kept.";
  }
  const done = job.frames_done;
  const total = job.frames_total;
  if (done === null) return "The video is ready.";
  if (total !== null && done < total) {
    return `Encoded ${done} of ${total} frames (${total - done} could not be read).`;
  }
  return `Encoded ${done} frames.`;
}
