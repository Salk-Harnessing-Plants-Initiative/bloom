// A video job as the browser sees it, and the words for each of its states.
// Client-safe: no Supabase import, so both buttons and both routes share it.

// The service's stage names (STAGE_* in services/workflows/video_queue.py).
export const VIDEO_JOB_STAGES = {
  downloading: "downloading",
  encoding: "encoding",
  uploading: "uploading",
} as const;

export type VideoJobStage = (typeof VIDEO_JOB_STAGES)[keyof typeof VIDEO_JOB_STAGES];

export type VideoJobStatus = "queued" | "rendering" | "rendered" | "kept" | "failed";

export type VideoJob = {
  id: string;
  status: VideoJobStatus;
  stage: string | null;
  frames_done: number | null;
  frames_total: number | null;
  error_code: string | null;
  queued_ahead: number | null;
  created_at: string;
  updated_at: string;
};

const STATUSES: readonly string[] = ["queued", "rendering", "rendered", "kept", "failed"];

const numberOrNull = (v: unknown) => (typeof v === "number" ? v : null);
const stringOrNull = (v: unknown) => (typeof v === "string" ? v : null);

/** A job read field by field from an untrusted body, or null if it is not one. */
export function toVideoJob(value: unknown): VideoJob | null {
  if (typeof value !== "object" || value === null) return null;
  const r = value as Record<string, unknown>;
  if (typeof r.id !== "string" || typeof r.status !== "string") return null;
  if (!STATUSES.includes(r.status)) return null;
  return {
    id: r.id,
    status: r.status as VideoJobStatus,
    stage: stringOrNull(r.stage),
    frames_done: numberOrNull(r.frames_done),
    frames_total: numberOrNull(r.frames_total),
    error_code: stringOrNull(r.error_code),
    queued_ahead: numberOrNull(r.queued_ahead),
    created_at: stringOrNull(r.created_at) ?? "",
    updated_at: stringOrNull(r.updated_at) ?? "",
  };
}

export function isActiveJob(job: VideoJob | null): job is VideoJob {
  return job?.status === "queued" || job?.status === "rendering";
}

/** "Queued — 2 ahead", or "Queued — next up". */
export function queuedNote(job: VideoJob): string {
  const ahead = job.queued_ahead ?? 0;
  return ahead > 0 ? `Queued — ${ahead} ahead` : "Queued — next up";
}

/** The download count while it is countable, else null. */
export function countedFrames(
  job: VideoJob | null
): { done: number; total: number } | null {
  if (job?.status !== "rendering" || job.stage !== VIDEO_JOB_STAGES.downloading)
    return null;
  const total = job.frames_total ?? 0;
  if (total <= 0) return null;
  return { done: Math.min(job.frames_done ?? 0, total), total };
}

/** What a rendering job is doing, in words. */
export function renderingNote(job: VideoJob): string {
  const counted = countedFrames(job);
  if (counted) return `Downloading frame ${counted.done} of ${counted.total}`;
  if (job.stage === VIDEO_JOB_STAGES.encoding) return "Encoding the video…";
  if (job.stage === VIDEO_JOB_STAGES.uploading) return "Saving the video…";
  return "Starting…";
}

const BLOOM_TEAM = "let the Bloom team know";

// Shared by both kinds of video.
const COMMON_FAILURES: Record<string, string> = {
  storage_unavailable: "Storage could not be reached, so nothing was changed. Try again shortly.",
  database_unavailable: "The database could not be reached, so nothing was changed. Try again shortly.",
  encode_failed: `The video could not be encoded. Try again — if it keeps happening, ${BLOOM_TEAM}.`,
  redelivered: `The render stopped three times without finishing. Please ${BLOOM_TEAM}.`,
  stalled: `The render stopped making progress and was abandoned. Try again — if it keeps happening, ${BLOOM_TEAM}.`,
  unexpected: `The video could not be made. Try again — if it keeps happening, ${BLOOM_TEAM}.`,
};

export const PLATE_FAILURES: Record<string, string> = {
  ...COMMON_FAILURES,
  no_frames: "This plate has no captures with an image for this wave yet.",
  too_large: `This plate has more captures than one video can hold. Please ${BLOOM_TEAM}.`,
  unusable_plate: "This plate's name cannot be used to store a video.",
  frame_too_large: `One of this plate's images is too large to render. Please ${BLOOM_TEAM}.`,
  frame_depth_unsupported: `One of this plate's images has a bit depth the video cannot use. Please ${BLOOM_TEAM}.`,
  frame_size_mismatch: `This plate's images are not all the same size. Please ${BLOOM_TEAM}.`,
  frame_unreadable: `One of this plate's images could not be read. Try again — if it keeps happening, ${BLOOM_TEAM}.`,
  not_stored: "The video was made but could not be saved. Try again shortly.",
  not_recorded: `The video may not have been saved or recorded. Try again — if it keeps happening, ${BLOOM_TEAM}.`,
  plate_mismatch: `The video could not be stored for this plate. Please ${BLOOM_TEAM}.`,
};

export const SCAN_FAILURES: Record<string, string> = {
  ...COMMON_FAILURES,
  no_images: "This scan has no images to make a video from.",
  scan_not_in_experiment: "This scan was not found in this experiment.",
  no_frames_encoded: `None of this scan's images could be read. Please ${BLOOM_TEAM}.`,
  not_signed: "The video was saved but a link to it could not be made. Reload the page.",
};

/** The failure in words, with a job reference a support request can quote. */
export function jobFailureMessage(
  job: VideoJob,
  sentences: Record<string, string>
): string {
  const sentence =
    (job.error_code && Object.hasOwn(sentences, job.error_code)
      ? sentences[job.error_code]
      : null) ?? COMMON_FAILURES.unexpected;
  return `${sentence} (job ${job.id.slice(0, 8)})`;
}

// Why a button stopped asking before its job finished.
export const POLL_SIGNED_OUT = "Your session has expired. Sign in again to see how this video turned out.";
export const POLL_LOST = "Lost track of this video. Reload the page to check on it.";

// How often a button asks how its job is going, and how long one ask may take.
// The timeout stays under the interval so a hung ask never overlaps the next.
export const VIDEO_JOB_POLL_INTERVAL_MS = 3_000;
export const VIDEO_JOB_POLL_TIMEOUT_MS = 2_500;
// Asks that may fail in a row before a button stops and says so: about 30 s.
export const VIDEO_JOB_MAX_FAILED_POLLS = 10;
