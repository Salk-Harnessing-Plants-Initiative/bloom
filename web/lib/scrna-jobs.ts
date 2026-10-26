/**
 * Shared pieces of the scRNA job form: the choices it offers (ready genome versions), the
 * dataset details saved with the run, the shape of a started run, and the messages shown for a
 * refused start.
 */

import type { Database } from "@/lib/database.types";

/** A ready genome version a run can build its reference from, as `bloomctl genome list` shows it. */
export type GenomeVersionOption = {
  id: number;
  genome: string;
  version: number;
  assembly: string | null;
  annotation: string | null;
};

/** A ready version row as read with its genome's name. */
export type ReadyVersionRow = {
  id: number;
  version: number;
  assembly: string | null;
  annotation: string | null;
  genome_references: { name: string } | null;
};

/** The ready versions, by genome name and newest version first; rows without a genome are left out. */
export function genomeVersionOptions(rows: ReadyVersionRow[]): GenomeVersionOption[] {
  return rows
    .filter((r) => r.genome_references?.name)
    .map((r) => ({
      id: r.id,
      genome: r.genome_references!.name,
      version: r.version,
      assembly: r.assembly,
      annotation: r.annotation,
    }))
    .sort((a, b) => a.genome.localeCompare(b.genome) || b.version - a.version);
}

/** How the form names a version, e.g. "athaliana_col0 v2 — TAIR10 / Araport11". */
export function genomeVersionLabel(option: GenomeVersionOption): string {
  const detail = [option.assembly, option.annotation].filter(Boolean).join(" / ");
  const name = `${option.genome} v${option.version}`;
  return detail ? `${name} — ${detail}` : name;
}

/** One free key/value row in the form, e.g. tissue = root. */
export type AttributeRow = { key: string; value: string };

/** The library's Cell Ranger chemistry: "auto" lets Cell Ranger detect it; a 10x Multiome RNA
 * library needs ARC-v1 named. The workflows service and the database accept the same values. */
export type Chemistry = "auto" | "ARC-v1";

export const LIBRARY_TYPES: readonly { chemistry: Chemistry; label: string }[] = [
  { chemistry: "auto", label: "10x gene expression (kit detected automatically)" },
  { chemistry: "ARC-v1", label: "10x Multiome — RNA part" },
];

/** How the run page names a run that left the kit to Cell Ranger. */
const DETECTED_LIBRARY_LABEL = "10x gene expression (detected by Cell Ranger)";

/** A run's library type for the run page. */
export function libraryTypeLabel(chemistry: string | null | undefined): string {
  if (!chemistry || chemistry === "auto") return DETECTED_LIBRARY_LABEL;
  return LIBRARY_TYPES.find((t) => t.chemistry === chemistry)?.label ?? `Chemistry ${chemistry}`;
}

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

// The loader's rule (bloomctl's SAFE_DATASET_NAME), as a storage path needs; tests/unit pins it.
const DATASET_NAME_RULE = /^[A-Za-z0-9._ -]+$/;
const DOTS_ONLY = /^\.+$/;

/** What the dataset name may contain, shown beside the field before anything is typed. */
export const DATASET_NAME_ALLOWED = "Letters A–Z, digits, spaces, . _ and -";

/** How a refused character is shown: as itself, or by code point when it can't be seen. */
function shownChar(char: string): string {
  return /^[\p{L}\p{N}\p{P}\p{S}]$/u.test(char)
    ? char
    : `U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}`;
}

/** What's wrong with the (trimmed) dataset name in a few words, or null if it can be loaded. */
export function datasetNameProblem(name: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return null;
  if (!DATASET_NAME_RULE.test(trimmed)) {
    const refused = [...new Set([...trimmed].filter((c) => !DATASET_NAME_RULE.test(c)))];
    return `Not allowed: ${refused.map(shownChar).join(" ")}`;
  }
  if (DOTS_ONLY.test(trimmed)) return "Can't be only dots";
  return null;
}

/** Why the details can't be submitted yet, or null if they can. */
export function datasetDetailsProblem(details: DatasetDetails): string | null {
  if (details.speciesId == null) return "Choose a species.";
  if (!details.datasetName.trim()) return "Enter a dataset name.";
  const nameProblem = datasetNameProblem(details.datasetName);
  if (nameProblem) return nameProblem;
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
  genome_version_id: number;
  // The genome version as <genome>.v<N>.
  reference: string;
  run_key: string;
};

export function isStartedRun(value: unknown): value is StartedRun {
  const v = value as Partial<StartedRun> | null;
  return (
    typeof v?.run_id === "number" &&
    typeof v.sample === "string" &&
    typeof v.genome_version_id === "number" &&
    typeof v.reference === "string" &&
    typeof v.run_key === "string"
  );
}

/** The message shown when starting a run fails, preferring the service's own wording. */
export function startRunErrorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "Sign in to start a job.";
  if (status === 429) return "Too many requests. Wait a minute and try again.";
  if (status === 409) return "This sample already has a run on that genome, or these reads were already run on it. Choose another sample name or genome.";
  if (status === 422) return "The sample name or the genome version can't be used.";
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
