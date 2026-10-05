// A Cell Ranger run's FASTQs read from an S3 folder: the URL, the check the workflows service
// runs on it, and the layout the form explains.

import { formatBytes } from "@/lib/scrna-jobs";

// The same rules the service and the database apply.
const FOLDER_URL = /^s3:\/\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/([A-Za-z0-9!_.*'()-]+\/)+$/;
const DOT_SEGMENT = /\/\.{1,2}\//;
const MAX_URL = 1024;
const BUCKET_ROOT = /^s3:\/\/[^/]+\/$/;
// A pasted file rather than its folder, e.g. s3://lab/run42/col0_S1_L001_R1_001.fastq.gz
const FASTQ_FILE = /\.(fastq|fq)(\.gz)?\/?$/i;

/** The most FASTQs a run takes; the service and the database refuse more. */
export const MAX_FOLDER_FILES = 96;

// The service's 409 when the folder no longer matches the files its check showed. Other
// 409s (the run already exists) aren't helped by checking the folder again.
const FOLDER_CHANGED = "changed since it was checked";

/** How long the form waits after the URL stops changing before checking it. */
export const FOLDER_CHECK_DELAY_MS = 500;

export const FOLDER_EXAMPLE = {
  url: "s3://lab-data/col0_root_rep1/",
  files: [
    "col0_root_rep1_S1_L001_R1_001.fastq.gz",
    "col0_root_rep1_S1_L001_R2_001.fastq.gz",
    "col0_root_rep1_S1_L001_I1_001.fastq.gz (optional)",
  ],
};

export type FolderFile = { name: string; size: number; etag: string };

/** What the workflows service answers when a folder passes its check. */
export type FolderCheck = {
  fastq_url: string;
  sample: string;
  lanes: number[];
  files: FolderFile[];
  file_count: number;
  total_bytes: number;
};

/** The URL with surrounding spaces dropped and a trailing "/" added, as the service stores it. */
export function normaliseFolderUrl(url: string): string {
  const trimmed = url.trim();
  return trimmed && !trimmed.endsWith("/") ? `${trimmed}/` : trimmed;
}

/** Whether the URL is worth checking: an S3 folder in the accepted form. */
export function looksLikeFolder(url: string): boolean {
  const normalised = normaliseFolderUrl(url);
  return (
    normalised.length <= MAX_URL &&
    FOLDER_URL.test(normalised) &&
    !DOT_SEGMENT.test(normalised) &&
    !FASTQ_FILE.test(normalised)
  );
}

/** Why a typed URL can't be checked yet, or null when it can (or nothing is typed). */
export function folderUrlProblem(url: string): string | null {
  const normalised = normaliseFolderUrl(url);
  if (!normalised || looksLikeFolder(normalised)) return null;
  if (/^https?:\/\//i.test(normalised)) {
    return "Use the s3:// form (s3://bucket/folder/), not a web address.";
  }
  if (!normalised.startsWith("s3://")) return "Enter an S3 folder, starting with s3://";
  if (BUCKET_ROOT.test(normalised)) {
    return "Give the folder inside the bucket, e.g. s3://bucket/folder/";
  }
  if (FASTQ_FILE.test(normalised)) {
    return "That's a file. Give the folder that holds the FASTQs, ending in /";
  }
  if (normalised.length > MAX_URL) return `An S3 folder URL can be at most ${MAX_URL} characters.`;
  return "An S3 folder looks like s3://bucket/folder/: a lower-case bucket, then folder names of letters, digits and !_.*'()- (no spaces or other characters)";
}

/** Whether a refused start is the service's "the folder changed since its check". */
export function isFolderChangedRefusal(status: number, detail: unknown): boolean {
  return status === 409 && typeof detail === "string" && detail.includes(FOLDER_CHANGED);
}

export function isFolderCheck(value: unknown): value is FolderCheck {
  const v = value as Partial<FolderCheck> | null;
  return (
    typeof v?.fastq_url === "string" &&
    typeof v.sample === "string" &&
    Array.isArray(v.lanes) &&
    v.lanes.every((lane) => typeof lane === "number") &&
    Array.isArray(v.files) &&
    v.files.every(
      (f) =>
        typeof f?.name === "string" && typeof f.size === "number" && typeof f.etag === "string"
    ) &&
    typeof v.file_count === "number" &&
    typeof v.total_bytes === "number"
  );
}

/** One line for a passed check, e.g. "col0 · 2 lanes · 4 files · 38.2 GB". */
export function folderSummary(check: FolderCheck): string {
  const lanes = check.lanes.length;
  return [
    check.sample,
    `${lanes} lane${lanes === 1 ? "" : "s"}`,
    `${check.file_count} file${check.file_count === 1 ? "" : "s"}`,
    formatBytes(check.total_bytes),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The message shown when the check fails, preferring the service's own wording. */
export function folderCheckErrorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "Sign in to check a folder.";
  if (status === 429) return "Too many checks. Wait a minute and try again.";
  return "Couldn't check the folder. Try again shortly.";
}
