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
  { id: "stage-reference", label: "Stage reference" },
  { id: "stage", label: "Stage FASTQs" },
  { id: "qc", label: "FastQC" },
  { id: "count", label: "Cell Ranger count" },
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

// The pipeline's own exit codes (argo/scrna/cellranger/run-count.sh).
const EXIT_SENTENCES: Record<number, string> = {
  3: "The reference genome folder, or its reference.json, wasn't found.",
  4: "No FASTQ files were found in the sample's folder.",
  5: "Cell Ranger count failed; its log has the details.",
  6: "The sample name can't be used as a Cell Ranger run id.",
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

/** Whether a step has a pod, and so a log that can be asked for. */
export function stepStarted(run: RnaseqRun, step: StepId): boolean {
  return Boolean(field(run.step_pods, step));
}

/** Each Cell Ranger step's state, from the run's status and current step. */
export function stepStates(run: RnaseqRun): Record<StepId, StepState> {
  const current = CELLRANGER_STEPS.findIndex((s) => s.id === run.current_step);
  const states = {} as Record<StepId, StepState>;
  CELLRANGER_STEPS.forEach((step, index) => {
    let state: StepState;
    if (run.status === "succeeded") state = "done";
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
  if (run.exit_code != null && EXIT_SENTENCES[run.exit_code]) return EXIT_SENTENCES[run.exit_code];
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
