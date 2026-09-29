/**
 * Shared pieces of the scRNA job form: the choices it offers (registered samples and
 * references), the dataset details saved with the run, the shape of a started run, and
 * the messages shown for a refused start.
 */

import type { Database } from "@/lib/database.types";

export type RnaseqSample = Pick<
  Database["public"]["Tables"]["rnaseq_samples"]["Row"],
  "name" | "source" | "fastq_count" | "total_bytes"
>;

export type RnaseqReference = Pick<
  Database["public"]["Tables"]["rnaseq_references"]["Row"],
  "name" | "description"
>;

/** One free key/value row in the form, e.g. tissue = root. */
export type AttributeRow = { key: string; value: string };

/** What the form collects about the dataset the run will produce. */
/** HPI's own data, or a public dataset from a paper or archive. */
export type DataOrigin = "hpi" | "public";

export type DatasetDetails = {
  speciesId: number | null;
  datasetName: string;
  accession: string;
  experimentName: string;
  origin: DataOrigin;
  sourceUrl: string;
  citation: string;
  attributes: AttributeRow[];
};

const SOURCE_URL_MAX = 2000;
const CITATION_MAX = 500;

/** Saved on the run as `metadata` and used when its results are loaded as a dataset. */
export type RunMetadata = {
  species_id: number;
  dataset_name: string;
  accession?: string;
  experiment_name?: string;
  origin: DataOrigin;
  // A public dataset's paper, DOI or archive record; becomes scrna_datasets.url.
  source_url?: string;
  citation?: string;
  attributes?: Record<string, string>;
};

/** Whether `value` is an http(s) link. */
function isWebLink(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

/** Why the details can't be submitted yet, or null if they can. */
export function datasetDetailsProblem(details: DatasetDetails): string | null {
  if (details.speciesId == null) return "Choose a species.";
  if (!details.datasetName.trim()) return "Enter a dataset name.";
  if (details.origin === "public") {
    const sourceUrl = details.sourceUrl.trim();
    if (!sourceUrl) return "Enter the public dataset's source link.";
    if (!isWebLink(sourceUrl) || sourceUrl.length > SOURCE_URL_MAX) {
      return "The source link must be a web address starting with https://.";
    }
    if (details.citation.trim().length > CITATION_MAX) {
      return `The citation is at most ${CITATION_MAX} characters.`;
    }
  }
  const seen = new Set<string>();
  for (const row of details.attributes) {
    const key = row.key.trim();
    if (!key && !row.value.trim()) continue;
    if (!key) return "Every extra field needs a name.";
    if (seen.has(key)) return `The field "${key}" is listed twice.`;
    seen.add(key);
  }
  return null;
}

/** The run's metadata, trimmed, with empty optional fields and blank rows left out. */
export function buildRunMetadata(details: DatasetDetails): RunMetadata {
  if (details.speciesId == null) throw new Error("species is required");
  const metadata: RunMetadata = {
    species_id: details.speciesId,
    dataset_name: details.datasetName.trim(),
    origin: details.origin,
  };
  const accession = details.accession.trim();
  if (accession) metadata.accession = accession;
  const experimentName = details.experimentName.trim();
  if (experimentName) metadata.experiment_name = experimentName;
  if (details.origin === "public") {
    metadata.source_url = details.sourceUrl.trim();
    const citation = details.citation.trim();
    if (citation) metadata.citation = citation;
  }
  const attributes: Record<string, string> = {};
  for (const row of details.attributes) {
    const key = row.key.trim();
    if (key) attributes[key] = row.value.trim();
  }
  if (Object.keys(attributes).length) metadata.attributes = attributes;
  return metadata;
}

/** What the workflows service answers when it queues a Cell Ranger run. */
export type StartedRun = {
  run_id: number;
  sample: string;
  reference: string;
  run_key: string;
};

export function isStartedRun(value: unknown): value is StartedRun {
  const v = value as Partial<StartedRun> | null;
  return (
    typeof v?.run_id === "number" &&
    typeof v.sample === "string" &&
    typeof v.reference === "string" &&
    typeof v.run_key === "string"
  );
}

/** The message shown when starting a run fails, preferring the service's own wording. */
export function startRunErrorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "Sign in to start a job.";
  if (status === 429) return "Too many requests. Wait a minute and try again.";
  if (status === 422) return "The sample or reference name isn't valid.";
  if (status === 503) return "The job service isn't available right now.";
  return "Couldn't start the job. Try again shortly.";
}

/** A byte count as a short size, e.g. 31.4 GB. */
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}
