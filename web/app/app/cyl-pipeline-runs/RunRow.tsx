"use client";

/** One row of the runs list. */

import Link from "next/link";
import { RunState } from "@/components/cyl-pipeline/RunState";
import { formatElapsed } from "@/lib/cyl-pipeline/elapsed";
import type { RunExperiment } from "@/lib/cyl-pipeline/queries";
import type { RunRow as Run } from "@/lib/cyl-pipeline/realtime-reducer";
import { requesterText, targetText } from "@/lib/cyl-pipeline/run-text";

export function RunRow({
  run,
  experiments,
  currentUserId,
  now,
}: {
  run: Run;
  experiments: RunExperiment[] | undefined;
  currentUserId: string | null;
  now: number | null;
}) {
  const href = `/app/cyl-pipeline-runs/${run.id}`;
  return (
    <li data-testid={`run-${run.id}`} className="grid grid-cols-12 gap-4 py-3 text-sm">
      <div className="col-span-2">
        <Link href={href} className="font-medium text-lime-700 hover:underline">
          Run {run.id}
        </Link>
        <div className="text-xs text-stone-500">{now === null ? "" : formatElapsed(run.created_at, now)}</div>
      </div>
      <div className="col-span-3">
        <div>{targetText(run)}</div>
        <div className="text-xs text-stone-500">{requesterText(run.requested_by, currentUserId)}</div>
      </div>
      <div className="col-span-3 flex flex-wrap gap-x-2">
        {(experiments ?? []).map((e) =>
          e.species_id == null ? (
            <span key={e.experiment_id}>{e.name ?? `Experiment ${e.experiment_id}`}</span>
          ) : (
            <Link
              key={e.experiment_id}
              href={`/app/phenotypes/${e.species_id}/${e.experiment_id}`}
              className="text-lime-700 hover:underline"
            >
              {e.name ?? `Experiment ${e.experiment_id}`}
            </Link>
          ),
        )}
      </div>
      <div className="col-span-4">
        <RunState run={run} failedHref={`${href}?status=failed`} />
      </div>
    </li>
  );
}
