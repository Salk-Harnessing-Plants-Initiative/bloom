/**
 * RNA-seq runs as the Timeline page shows them: reading them, their Cell Ranger steps,
 * and plain words for how a run ended. Runs are rows of `rnaseq_runs`, written by the
 * start API, the worker and the status poller.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database, Json } from "@/lib/database.types";

type RunRow = Database["public"]["Tables"]["rnaseq_runs"]["Row"];

export type RnaseqRun = Pick<
  RunRow,
  | "id"
  | "workflow_type"
  | "status"
  | "current_step"
  | "params"
  | "metadata"
  | "requested_by"
  | "argo_workflow_name"
  | "step_pods"
  | "exit_code"
  | "message"
  | "created_at"
  | "submitted_at"
  | "completed_at"
>;

export type RunStatus = "queued" | "submitted" | "running" | "succeeded" | "skipped" | "failed";

export const RUN_COLUMNS =
  "id, workflow_type, status, current_step, params, metadata, requested_by, " +
  "argo_workflow_name, step_pods, exit_code, message, created_at, submitted_at, completed_at";

// How many runs the list shows at first.
export const RUNS_PAGE_SIZE = 50;

/** Cell Ranger's steps in the order they run, as the poller reports them. */
export const CELLRANGER_STEPS = [
  { id: "fetch-sra", label: "Download from SRA" },
  { id: "stage-reference", label: "Stage reference" },
  { id: "stage", label: "Stage FASTQs" },
  { id: "qc", label: "FastQC" },
  { id: "count", label: "Cell Ranger count" },
  { id: "preprocess", label: "Filter and normalise" },
  { id: "cluster", label: "Cluster and UMAP" },
  { id: "build-h5ad", label: "Build the .h5ad" },
  { id: "cleanup", label: "Clean up" },
] as const;

export type StepId = (typeof CELLRANGER_STEPS)[number]["id"];
export type StepState = "done" | "running" | "failed" | "waiting" | "not-run" | "skipped";

export const STATUS_LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  submitted: "Submitted",
  running: "Running",
  succeeded: "Succeeded",
  skipped: "Skipped",
  failed: "Failed",
};

// The pipeline's own exit codes: argo/scrna/cellranger/ (run-count, fetch-sra) and
// argo/scrna/analysis/ (preprocess, cluster, build-h5ad).
const EXIT_SENTENCES: Record<number, string> = {
  3: "The reference genome folder, or its reference.json, wasn't found.",
  4: "No FASTQ files were found in the sample's folder.",
  5: "Cell Ranger count failed; its log has the details.",
  6: "The sample name can't be used as a Cell Ranger run id.",
  7: "The FASTQ file names don't follow Illumina's naming (<name>_S1_L001_R1_001.fastq.gz), or a lane lacks R1 or R2.",
  13: "Fewer than 50 cells passed the filters, too few to cluster.",
  14: "Cell Ranger's count matrix wasn't found for the analysis steps.",
  15: "An analysis step's results didn't fit with the others; its log has the details.",
};
// fetch-sra reuses some codes with its own meaning.
const FETCH_SRA_EXIT_SENTENCES: Record<number, string> = {
  6: "The sample name or the SRA run IDs can't be used.",
  7: "The downloaded FASTQs couldn't be named the Illumina way.",
  10: "A run couldn't be downloaded from SRA, or storage couldn't be checked. Start the run again; if it fails again, check the run IDs are public.",
  11: "An SRA run's reads don't look like 10x gene-expression reads; the Download from SRA step's log says which run and why.",
  12: "The sample's folder already holds other FASTQs; choose another sample name.",
};

// The stage step of a run that reads an S3 folder (stage-fastqs).
const FOLDER_STAGE_EXIT_SENTENCES: Record<number, string> = {
  4: "No FASTQ files were found in the run's S3 folder, even after waiting for an upload to finish.",
  6: "The run's S3 folder or its file list can't be used.",
  7: "The FASTQ file names in the S3 folder don't follow Illumina's naming (<name>_S1_L001_R1_001.fastq.gz), or a lane lacks R1 or R2.",
  8: "The S3 folder changed after the run was started, so its reads weren't used; the Stage FASTQs step's log says which file. Start a new run on the folder as it is now.",
  9: "The FASTQs in the S3 folder are named for another sample than the run's; the Stage FASTQs step's log names it.",
  10: "The S3 folder couldn't be listed or copied. Check it's still public and start the run again.",
};

function field(value: Json | null, key: string): unknown {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)[key]
    : undefined;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function runSample(run: RnaseqRun): string | null {
  return text(field(run.params, "sample"));
}

export function runReference(run: RnaseqRun): string | null {
  return text(field(run.params, "reference"));
}

export function runDatasetName(run: RnaseqRun): string | null {
  return text(field(run.metadata, "dataset_name"));
}

export function runSpeciesId(run: RnaseqRun): number | null {
  const id = field(run.metadata, "species_id");
  return typeof id === "number" ? id : null;
}

/** The run's free "Other details" rows, as name/value pairs. */
export function runAttributes(run: RnaseqRun): [string, string][] {
  const attributes = field(run.metadata, "attributes");
  if (!attributes || typeof attributes !== "object" || Array.isArray(attributes)) return [];
  return Object.entries(attributes as Record<string, unknown>).map(([k, v]) => [k, String(v)]);
}

export function isFinished(status: string): boolean {
  return status === "succeeded" || status === "skipped" || status === "failed";
}

/** The run's SRA run IDs in lane order; empty unless it imports its sample from SRA. */
export function runSraRuns(run: RnaseqRun): string[] {
  const runs = field(run.params, "sra_runs");
  return Array.isArray(runs) ? runs.filter((r): r is string => typeof r === "string") : [];
}

/** The S3 folder the run reads its FASTQs from, or null. */
export function runFastqUrl(run: RnaseqRun): string | null {
  const url = field(run.params, "fastq_url");
  return typeof url === "string" ? url : null;
}

/** The steps this run goes through: fetch-sra only when it imports from SRA. */
export function runSteps(run: RnaseqRun): (typeof CELLRANGER_STEPS)[number][] {
  const imports = runSraRuns(run).length > 0;
  return CELLRANGER_STEPS.filter((step) => step.id !== "fetch-sra" || imports);
}

/** Whether a step has a pod, and so a log that can be asked for. */
export function stepStarted(run: RnaseqRun, step: StepId): boolean {
  return Boolean(field(run.step_pods, step));
}

/** Each of the run's steps' state, from the run's status and current step. */
export function stepStates(run: RnaseqRun): Partial<Record<StepId, StepState>> {
  const steps = runSteps(run);
  const current = steps.findIndex((s) => s.id === run.current_step);
  const states: Partial<Record<StepId, StepState>> = {};
  steps.forEach((step, index) => {
    let state: StepState;
    // A step with no pod never ran, e.g. one added after the run finished.
    if (run.status === "succeeded") state = stepStarted(run, step.id) ? "done" : "not-run";
    else if (run.status === "skipped") state = stepStarted(run, step.id) ? "done" : "skipped";
    else if (run.status === "failed") {
      if (current < 0) state = "not-run";
      else state = index < current ? "done" : index === current ? "failed" : "not-run";
    } else if (current < 0) state = "waiting";
    else state = index < current ? "done" : index === current ? "running" : "waiting";
    states[step.id] = state;
  });
  return states;
}

/** A sentence for how a failed run ended, or null. */
export function failureSentence(run: RnaseqRun): string | null {
  if (run.status !== "failed") return null;
  const sentences =
    run.current_step === "fetch-sra"
      ? FETCH_SRA_EXIT_SENTENCES
      : run.current_step === "stage" && runFastqUrl(run)
        ? { ...EXIT_SENTENCES, ...FOLDER_STAGE_EXIT_SENTENCES }
        : EXIT_SENTENCES;
  if (run.exit_code != null && sentences[run.exit_code]) return sentences[run.exit_code];
  return run.message;
}

/** The newest runs, optionally only one user's, newest first. */
export async function fetchRuns(
  supabase: SupabaseClient<Database>,
  { requestedBy = null, limit = RUNS_PAGE_SIZE }: { requestedBy?: string | null; limit?: number } = {}
): Promise<RnaseqRun[]> {
  let query = supabase
    .from("rnaseq_runs")
    .select(RUN_COLUMNS)
    .order("id", { ascending: false })
    .limit(limit);
  if (requestedBy) query = query.eq("requested_by", requestedBy);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []) as unknown as RnaseqRun[];
}

export async function fetchRun(
  supabase: SupabaseClient<Database>,
  runId: number
): Promise<RnaseqRun | null> {
  const { data, error } = await supabase
    .from("rnaseq_runs")
    .select(RUN_COLUMNS)
    .eq("id", runId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return (data as unknown as RnaseqRun | null) ?? null;
}

/** Who started each run, by email; runs whose starter can't be read are left out. */
export async function fetchRequesters(
  supabase: SupabaseClient<Database>,
  runIds: number[]
): Promise<Map<number, string>> {
  if (runIds.length === 0) return new Map();
  const { data, error } = await supabase.rpc("rnaseq_run_requesters", { p_run_ids: runIds });
  if (error || !data) return new Map();
  return new Map(data.map((r) => [r.run_id, r.email]));
}

/** A list with one run added or replaced, newest first, and runs `accept` rejects left out. */
export function upsertRun(
  runs: RnaseqRun[],
  run: RnaseqRun,
  accept: (run: RnaseqRun) => boolean = () => true
): RnaseqRun[] {
  const others = runs.filter((r) => r.id !== run.id);
  if (!accept(run)) return others;
  return [...others, run].sort((a, b) => b.id - a.id);
}
