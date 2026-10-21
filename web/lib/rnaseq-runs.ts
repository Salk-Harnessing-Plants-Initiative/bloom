/**
 * RNA-seq runs as the Timeline page shows them: reading them, their Cell Ranger steps,
 * and plain words for how a run ended. Runs are rows of `rnaseq_runs`, written by the
 * start API, the worker and the status poller.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database, Json } from "@/lib/database.types";
import {
  genomeVersionLabel,
  genomeVersionOptions,
  type ReadyVersionRow,
} from "@/lib/scrna-jobs";

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
  | "genome_version_id"
  | "dataset_id"
>;

export type RunStatus = "queued" | "submitted" | "running" | "succeeded" | "skipped" | "failed";

export const RUN_COLUMNS =
  "id, workflow_type, status, current_step, params, metadata, requested_by, " +
  "argo_workflow_name, step_pods, exit_code, message, created_at, submitted_at, completed_at, " +
  "genome_version_id, dataset_id";

// How many runs the list shows at first.
export const RUNS_PAGE_SIZE = 50;

/**
 * Cell Ranger's steps in the order the poller ranks them (services/workflows/rnaseq_status.py's
 * CELLRANGER_STEPS; a test keeps the two the same). The genome steps run alongside the reads'
 * steps and usually finish last, so they come just before count. stage-reference is the step
 * runs started before genomes came from Bloom have instead of the two genome steps.
 */
export const CELLRANGER_STEPS = [
  { id: "fetch-sra", label: "Download from SRA" },
  { id: "stage-reference", label: "Stage reference" },
  { id: "stage", label: "Stage FASTQs" },
  { id: "qc", label: "FastQC" },
  { id: "download-genome", label: "Download genome" },
  { id: "build-reference", label: "Build reference" },
  { id: "annotate-genome", label: "Annotate genome (GO & KEGG)" },
  { id: "count", label: "Cell Ranger count" },
  { id: "preprocess", label: "Filter and normalise" },
  { id: "cluster", label: "Cluster and UMAP" },
  { id: "de", label: "Differential expression" },
  { id: "build-h5ad", label: "Build the .h5ad" },
  { id: "load-dataset", label: "Load into Bloom" },
  { id: "go-pathways", label: "GO & pathways" },
  { id: "cleanup", label: "Clean up" },
] as const;

export type StepId = (typeof CELLRANGER_STEPS)[number]["id"];
export type StepState =
  | "done"
  | "running"
  | "retrying"
  | "failed"
  | "waiting"
  | "not-run"
  | "skipped";

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
  3: "The Cell Ranger reference wasn't found to count against.",
  4: "No FASTQ files were found in the sample's folder.",
  5: "Cell Ranger count failed; its log has the details.",
  6: "The sample name can't be used as a Cell Ranger run id.",
  7: "The FASTQ file names don't follow Illumina's naming (<name>_S1_L001_R1_001.fastq.gz), or a lane lacks R1 or R2.",
  13: "Fewer than 50 cells passed the filters, too few to cluster.",
  14: "Cell Ranger's count matrix wasn't found for the analysis steps.",
  15: "An analysis step's results didn't fit with the others; its log has the details.",
};
// The genome steps' exits mean something else at each, so the service's message is shown.
const MESSAGE_STEPS: ReadonlySet<string> = new Set(["download-genome", "build-reference"]);
// load-dataset (argo/scrna/analysis/load-dataset.sh) reuses 6 with its own meaning.
const LOAD_DATASET_EXIT_SENTENCES: Record<number, string> = {
  6: "The run's final .h5ad wasn't found, so there was nothing to load into Bloom; ask the Bloom admins.",
  16: "The pipeline has no Bloom credentials, so the dataset wasn't loaded; ask the Bloom admins.",
};
// fetch-sra reuses some codes with its own meaning.
const FETCH_SRA_EXIT_SENTENCES: Record<number, string> = {
  6: "The sample name or the SRA run IDs can't be used.",
  7: "The downloaded FASTQs couldn't be named the Illumina way.",
  10: "A run couldn't be downloaded from ENA or SRA; the Download from SRA step's log says why. Start the run again; if it fails again, check the run IDs are public.",
  11: "An SRA run's reads don't look like 10x gene-expression reads; the Download from SRA step's log says which run and why.",
};

// The stage step of a run that reads an S3 folder (stage-fastqs).
const FOLDER_STAGE_EXIT_SENTENCES: Record<number, string> = {
  4: "The FASTQs in the S3 folder were removed after the run was started; start a new run once they're back.",
  6: "The run's S3 folder or its recorded file list couldn't be used. This isn't something wrong with the folder; ask the Bloom admins.",
  7: "The FASTQ file names in the S3 folder don't follow Illumina's naming (<name>_S1_L001_R1_001.fastq.gz), or a lane lacks R1 or R2.",
  8: "The S3 folder changed after the run was started, so its reads weren't used; the Stage FASTQs step's log says which file. Start a new run on the folder as it is now.",
  9: "The FASTQs in the S3 folder are named for another sample than the run's; the Stage FASTQs step's log names it.",
  10: "The S3 folder couldn't be listed or copied; the Stage FASTQs step's log has S3's error. Check it's still public and its files aren't archived, then start the run again.",
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

/** The expression page of the dataset the run loaded, once the load has linked it. */
export function runDatasetHref(run: RnaseqRun): string | null {
  const speciesId = runSpeciesId(run);
  return run.dataset_id != null && speciesId != null ? `/app/expression/${speciesId}/${run.dataset_id}` : null;
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

// Steps the pipeline no longer has, and the steps that replaced them.
const RETIRED_STEPS: ReadonlySet<StepId> = new Set(["stage-reference"]);
const GENOME_STEPS: ReadonlySet<StepId> = new Set(["download-genome", "build-reference"]);
// GO steps run alongside the others; listed on runs that report each step's outcome.
const GO_STEPS: ReadonlySet<StepId> = new Set(["annotate-genome", "go-pathways"]);

// Where the poller keeps each started step's Argo phase in step_pods.
const PHASES_KEY = "_phases";
const PHASE_STATES: Record<string, StepState> = {
  Pending: "running",
  Running: "running",
  // A failed attempt with Argo's next one still to start.
  Retrying: "retrying",
  Succeeded: "done",
  Failed: "failed",
  Error: "failed",
};

/** Each started step's own Argo phase, or null for a run polled before steps reported one. */
function stepPhases(run: RnaseqRun): Record<string, unknown> | null {
  const phases = field(run.step_pods, PHASES_KEY);
  return phases && typeof phases === "object" && !Array.isArray(phases)
    ? (phases as Record<string, unknown>)
    : null;
}

/** Whether the run started before genomes came from Bloom, so it staged its reference instead. */
function stagedItsReference(run: RnaseqRun): boolean {
  return run.current_step === "stage-reference" || Boolean(field(run.step_pods, "stage-reference"));
}

/** The steps this run goes through: fetch-sra when it imports from SRA, stage otherwise (or when
 * an earlier SRA run staged its reads), and the genome steps, or stage-reference for an earlier run. */
export function runSteps(run: RnaseqRun): (typeof CELLRANGER_STEPS)[number][] {
  const imports = runSraRuns(run).length > 0;
  const staged = stagedItsReference(run);
  const stages = !imports || stepStarted(run, "stage");
  return CELLRANGER_STEPS.filter(
    (step) =>
      (step.id !== "fetch-sra" || imports) &&
      (step.id !== "stage" || stages) &&
      (!GO_STEPS.has(step.id) || stepPhases(run) !== null || stepStarted(run, step.id)) &&
      (staged ? !GENOME_STEPS.has(step.id) : !RETIRED_STEPS.has(step.id))
  );
}

/** Whether a step has a pod, and so a log that can be asked for. */
export function stepStarted(run: RnaseqRun, step: StepId): boolean {
  return Boolean(field(run.step_pods, step));
}

/** Each of the run's steps' state: from each step's own outcome when the run reports it, else
 * from the run's status and current step. */
export function stepStates(run: RnaseqRun): Partial<Record<StepId, StepState>> {
  const steps = runSteps(run);
  const phases = stepPhases(run);
  if (phases) return statesFromPhases(run, steps, phases);
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

// Steps run in parallel and a GO step can fail after the dataset loaded, so each step's
// state is its own phase; a step without one hasn't started.
function statesFromPhases(
  run: RnaseqRun,
  steps: readonly { id: StepId }[],
  phases: Record<string, unknown>
): Partial<Record<StepId, StepState>> {
  const finished = isFinished(run.status);
  const unstarted: StepState = run.status === "skipped" ? "skipped" : finished ? "not-run" : "waiting";
  const states: Partial<Record<StepId, StepState>> = {};
  for (const step of steps) {
    const phase = phases[step.id];
    const state = (typeof phase === "string" && PHASE_STATES[phase]) || unstarted;
    // The last poll can catch a step running that the run's end then stopped.
    states[step.id] = finished && (state === "running" || state === "retrying") ? "not-run" : state;
  }
  return states;
}

// Exits whose fixed sentence talks about the sample's own folder, which a folder run
// doesn't have; the service's message names what it copied instead.
const SAMPLE_FOLDER_EXITS = new Set([4, 7]);

/** A sentence for how a failed run ended, or null. */
export function failureSentence(run: RnaseqRun): string | null {
  if (run.status !== "failed") return null;
  if (run.current_step && MESSAGE_STEPS.has(run.current_step) && run.message) return run.message;
  if (runFastqUrl(run)) {
    // The service words a folder run's failures around its folder, e.g. naming the sample
    // for exit 9; the fixed sentences are only a fallback when it left no message.
    if (run.message && (run.current_step === "stage" || SAMPLE_FOLDER_EXITS.has(run.exit_code ?? -1))) {
      return run.message;
    }
    if (run.current_step === "stage") {
      return (run.exit_code != null && FOLDER_STAGE_EXIT_SENTENCES[run.exit_code]) || run.message;
    }
  }
  const sentences =
    run.current_step === "fetch-sra"
      ? FETCH_SRA_EXIT_SENTENCES
      : run.current_step === "load-dataset"
        ? LOAD_DATASET_EXIT_SENTENCES
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

/** The run's genome version as the form names it, e.g. "athaliana_col0 v2 — TAIR10 / Araport11";
 * null for a run started before genomes came from Bloom, or when it can't be read. */
export async function fetchGenomeLabel(
  supabase: SupabaseClient<Database>,
  run: RnaseqRun
): Promise<string | null> {
  if (run.genome_version_id == null) return null;
  const { data, error } = await supabase
    .from("genome_reference_versions")
    .select("id, version, assembly, annotation, genome_references(name)")
    .eq("id", run.genome_version_id)
    .maybeSingle();
  if (error || !data) return null;
  const [option] = genomeVersionOptions([data as unknown as ReadyVersionRow]);
  return option ? genomeVersionLabel(option) : null;
}

/** The name the run's dataset was loaded under, which may differ from the name on the form
 * (e.g. Root atlas_v2 when Root atlas was taken); null before the run is linked to it or when
 * it can't be read. */
export async function fetchDatasetName(
  supabase: SupabaseClient<Database>,
  run: RnaseqRun
): Promise<string | null> {
  if (run.dataset_id == null) return null;
  const { data, error } = await supabase.from("scrna_datasets").select("name").eq("id", run.dataset_id).maybeSingle();
  return error || !data ? null : data.name;
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
