"use client";

import type { StartedRun } from "@/lib/scrna-jobs";

/** Shown in place of the form once a run is queued, so it can't be started twice. */
export default function ScrnaJobQueued({
  run,
  datasetName,
  speciesLabel,
  startedBy,
  onStartAnother,
}: {
  run: StartedRun;
  datasetName: string;
  speciesLabel: string | null;
  startedBy: string | null;
  onStartAnother: () => void;
}) {
  return (
    <div role="status" className="space-y-4">
      <div>
        <p className="text-lg font-medium text-stone-800">
          Run <span className="tabular-nums">{run.run_id}</span> queued
        </p>
        <p className="mt-1 text-sm text-stone-700">
          <span className="font-medium">{run.sample}</span> against{" "}
          <span className="font-medium">{run.reference}</span>
          {" · "}
          {datasetName}
          {speciesLabel ? <> ({speciesLabel})</> : null}
        </p>
        {startedBy ? (
          <p className="mt-1 text-sm text-stone-500">Started by {startedBy}</p>
        ) : null}
      </div>
      <p className="text-sm text-stone-500">
        Cell Ranger count usually takes a few hours. The results are added to the
        Expression page when it finishes.
      </p>
      <div>
        <button
          type="button"
          onClick={onStartAnother}
          className="rounded-md bg-lime-700 px-4 py-2.5 text-sm font-medium text-stone-50 hover:bg-lime-800"
        >
          Start another run
        </button>
      </div>
    </div>
  );
}
