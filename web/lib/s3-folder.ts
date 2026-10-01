// A Cell Ranger run's FASTQs read from an S3 folder: the URL, the check the workflows service
// runs on it, and the layout the form explains.

import { formatBytes } from "@/lib/scrna-jobs";

// The same rules the service and the database apply.
const FOLDER_URL = /^s3:\/\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/([A-Za-z0-9!_.*'()-]+\/)+$/;
const DOT_SEGMENT = /\/\.{1,2}\//;
const MAX_URL = 1024;

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
    !DOT_SEGMENT.test(normalised)
  );
}

/** Why a typed URL can't be checked yet, or null when it can (or nothing is typed). */
export function folderUrlProblem(url: string): string | null {
  const normalised = normaliseFolderUrl(url);
  if (!normalised || looksLikeFolder(normalised)) return null;
  if (!normalised.startsWith("s3://")) return "Enter an S3 folder, starting with s3://";
  return "An S3 folder looks like s3://bucket/folder/: a lower-case bucket, then a folder with no spaces";
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
