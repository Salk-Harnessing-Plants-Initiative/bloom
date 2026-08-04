// Pure helpers for on-demand cyl scan video generation, split out so the id
// validation and the user-facing messaging are unit-testable without a network
// round-trip or a rendered component.

// The workflows endpoint's success payload.
export type ScanVideoResult = {
  scan_id: number;
  experiment_id: number;
  frames: number;
  frames_expected: number;
  truncated: boolean;
  regenerated: boolean;
  path: string;
  download_url: string;
};

// A route param that is safe to interpolate into the upstream URL. Anything
// non-integer is rejected rather than escaped: these land in a path segment, so
// a value like "1/../../health" would otherwise retarget the request.
export function parseId(value: string | undefined | null): number | null {
  if (typeof value !== "string" || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

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
  if (status >= 500) return "Video generation failed. Try again in a moment.";
  return `Could not generate the video (HTTP ${status}).`;
}

// One line describing what generation actually produced. Frames can go missing
// (an unreadable frame is skipped, not fatal) and very long scans are capped,
// so "done" on its own would overstate the result.
export function videoResultSummary(result: ScanVideoResult): string {
  const { frames, frames_expected, truncated, regenerated } = result;

  if (!regenerated) {
    return `Kept the existing video (${frames} frames) — it had at least as many frames as this run.`;
  }

  const parts: string[] = [];
  if (frames < frames_expected) {
    parts.push(
      `Encoded ${frames} of ${frames_expected} frames (${
        frames_expected - frames
      } could not be read).`
    );
  } else {
    parts.push(`Encoded ${frames} frames.`);
  }
  if (truncated) {
    parts.push(
      `The scan has more than ${frames_expected} frames; only the first ${frames_expected} were used.`
    );
  }
  return parts.join(" ");
}
