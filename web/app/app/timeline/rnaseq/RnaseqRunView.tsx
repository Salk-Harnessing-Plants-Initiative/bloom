"use client";

import Link from "next/link";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Change } from "@/lib/cyl-pipeline/realtime-reducer";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import {
  failureSentence,
  fetchRun,
  runAttributes,
  runDatasetName,
  runReference,
  runSample,
  runSpeciesId,
  stepStarted,
  runFastqUrl,
  runSraRuns,
  runSteps,
  stepStates,
  type RnaseqRun,
  type StepState,
} from "@/lib/rnaseq-runs";
import { sraRunUrl } from "@/lib/sra-runs";
import { StatusBadge, when } from "./run-labels";
import StepLog from "./StepLog";

const STEP_STATE: Record<StepState, { mark: string; label: string; className: string }> = {
  done: { mark: "✓", label: "Done", className: "text-lime-700" },
  running: { mark: "●", label: "Running", className: "text-sky-700" },
  failed: { mark: "✕", label: "Failed", className: "text-red-700" },
  waiting: { mark: "○", label: "Waiting", className: "text-stone-400" },
  "not-run": { mark: "–", label: "Not run", className: "text-stone-400" },
  skipped: { mark: "↷", label: "Skipped", className: "text-stone-500" },
};

function metadataText(run: RnaseqRun, key: string): string | null {
  const value =
    run.metadata && typeof run.metadata === "object" && !Array.isArray(run.metadata)
      ? (run.metadata as Record<string, unknown>)[key]
      : undefined;
  return typeof value === "string" && value ? value : null;
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 py-1 text-sm">
      <dt className="w-36 shrink-0 text-stone-500">{label}</dt>
      <dd className="min-w-0 break-words text-stone-800">{children}</dd>
    </div>
  );
}

/** One RNA-seq run: its steps, details and how it ended, kept current by Realtime. */
export default function RnaseqRunView({
  initialRun,
  startedBy,
  speciesLabel,
}: {
  initialRun: RnaseqRun;
  startedBy: string | null;
  speciesLabel: string | null;
}) {
  const live = useLiveSync<RnaseqRun>({
    topic: `rnaseq-run-${initialRun.id}`,
    bindings: [{ table: "rnaseq_runs", event: "UPDATE", filter: `id=eq.${initialRun.id}` }],
    initial: initialRun,
    snapshot: async () => (await fetchRun(createClientSupabaseClient(), initialRun.id)) ?? initialRun,
    apply: (run, change) => ({ ...run, ...(change as Change<RnaseqRun>).new }),
  });
  const run = live.view;
  const states = stepStates(run);
  const sraRuns = runSraRuns(run);
  const fastqUrl = runFastqUrl(run);
  const failure = failureSentence(run);
  const origin = metadataText(run, "origin");
  const sourceUrl = metadataText(run, "source_url");
  const attributes = runAttributes(run);
  const species = runSpeciesId(run) != null ? speciesLabel : null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <Link href="/app/timeline?panel=rnaseq" className="text-sm text-lime-700 underline-offset-4 hover:underline">
          ← All RNA-seq runs
        </Link>
        <LiveIndicator state={live.connection} onRefresh={live.refresh} />
      </div>

      <div className="mb-6 flex flex-wrap items-baseline gap-3">
        <h2 className="text-xl font-serif italic">Run {run.id}</h2>
        <StatusBadge status={run.status} />
        <span className="text-stone-700">
          {runSample(run) ?? "—"} against {runReference(run) ?? "—"}
        </span>
      </div>
      {fastqUrl ? (
        <p className="-mt-4 mb-6 text-sm text-stone-600">
          Reads from <span className="break-all font-mono">{fastqUrl}</span>
        </p>
      ) : null}
      {sraRuns.length ? (
        <p className="-mt-4 mb-6 text-sm text-stone-600">
          Imported from SRA:{" "}
          {sraRuns.map((id, i) => (
            <span key={id}>
              {i ? ", " : null}
              <a href={sraRunUrl(id)} target="_blank" rel="noreferrer" className="font-mono text-lime-800 underline">
                {id}
              </a>
            </span>
          ))}
        </p>
      ) : null}

      {failure ? (
        <div role="alert" className="mb-6 max-w-2xl rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {run.exit_code != null ? <span className="font-medium">Exit {run.exit_code}: </span> : null}
          {failure}
          {run.message && run.message !== failure ? (
            <span className="mt-1 block text-red-700">{run.message}</span>
          ) : null}
        </div>
      ) : run.status === "skipped" ? (
        <p className="mb-6 max-w-2xl text-sm text-stone-600">
          Skipped: the results for this sample and reference already existed.
        </p>
      ) : null}

      <h3 className="mb-2 text-xs uppercase tracking-widest text-stone-500">Steps</h3>
      <ol className="mb-8 max-w-3xl divide-y divide-stone-200 border-y border-stone-200">
        {runSteps(run).map((step) => {
          const state = STEP_STATE[states[step.id] ?? "waiting"];
          return (
            <li key={step.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
              <span aria-hidden className={`w-4 text-center ${state.className}`}>
                {state.mark}
              </span>
              <span className="w-44 text-sm text-stone-800">{step.label}</span>
              <span className={`w-20 text-xs ${state.className}`}>{state.label}</span>
              {stepStarted(run, step.id) ? (
                <StepLog runId={run.id} step={step.id} running={states[step.id] === "running"} />
              ) : null}
            </li>
          );
        })}
      </ol>

      <h3 className="mb-2 text-xs uppercase tracking-widest text-stone-500">Details</h3>
      <dl className="max-w-3xl">
        <Detail label="Dataset">{runDatasetName(run) ?? "—"}</Detail>
        <Detail label="Species">{species ?? "—"}</Detail>
        {metadataText(run, "accession") ? <Detail label="Accession">{metadataText(run, "accession")}</Detail> : null}
        {metadataText(run, "experiment_name") ? (
          <Detail label="Experiment">{metadataText(run, "experiment_name")}</Detail>
        ) : null}
        <Detail label="Data from">
          {origin === "public" ? "Public dataset" : origin === "hpi" ? "HPI" : "—"}
          {origin === "public" && sourceUrl ? ` · ${sourceUrl}` : ""}
          {origin === "public" && metadataText(run, "citation") ? ` · ${metadataText(run, "citation")}` : ""}
        </Detail>
        {attributes.map(([name, value]) => (
          <Detail key={name} label={name}>
            {value || "—"}
          </Detail>
        ))}
        <Detail label="Started by">{startedBy ?? "—"}</Detail>
        <Detail label="Queued">{when(run.created_at)}</Detail>
        <Detail label="Submitted">{when(run.submitted_at)}</Detail>
        <Detail label="Finished">{when(run.completed_at)}</Detail>
        <Detail label="Argo workflow">{run.argo_workflow_name ?? "—"}</Detail>
      </dl>
    </div>
  );
}
