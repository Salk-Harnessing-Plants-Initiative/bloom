/**
 * Counts-first display state of a pipeline run (design D3).
 *
 * `status` alone misleads: a run can be `complete` with failures (#857),
 * `partial` isn't terminal, and runs freeze in `running`/`queued` (#706,
 * #710). Failed and written scan rows are final, so once D + F = N the run is
 * finished, whatever its status says. `completed_at` is ignored (dispatch
 * stamps it before any pipeline outcome, and a run concluded `partial` before
 * fix-cyl-poller-unconcluded-runs carries roughly that change's deploy time)
 * and `reused_count` is never shown.
 */

export const RUN_STATUSES = ["queued", "submitted", "running", "complete", "partial", "failed"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const SCAN_STATUSES = ["queued", "predicted", "written", "reused", "failed"] as const;
export type ScanStatus = (typeof SCAN_STATUSES)[number];

/** The run columns the display state reads. */
export interface RunCounts {
  status: string;
  scan_count: number;
  done_count: number;
  failed_count: number;
  error_message?: string | null;
}

export type RunTone = "empty" | "succeeded" | "finished-with-failures" | "failed" | "ended" | "partial" | "active" | "unknown";

export interface RunDisplay {
  label: string;
  /** The raw `status`, always shown as secondary text. */
  rawStatus: string;
  tooltip: string;
  /** The run's `error_message`, only when `status = 'failed'`. */
  errorMessage: string | null;
  tone: RunTone;
  counts: { N: number; D: number; F: number; U: number };
}

const STAGES: Record<string, { label: string; tooltip: string }> = {
  queued: {
    label: "Queued",
    tooltip:
      "Not every batch has been sent to the cluster yet; a run stays queued until every batch has been sent or has failed to send, so some may already be done. A long wait may mean the dispatcher is down, or that the trigger stopped before queueing the run's scans.",
  },
  submitted: {
    label: "Submitted",
    tooltip: "Every batch has been sent to the cluster. Results appear as each batch of up to 25 scans finishes.",
  },
  running: {
    label: "Running",
    tooltip: "The cluster is processing this run's batches. Counts often stay at 0 until a batch of up to 25 scans finishes.",
  },
};

const whole = (n: number) => (Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0);

export function runDisplay(run: RunCounts): RunDisplay {
  const N = whole(run.scan_count);
  const F = Math.min(whole(run.failed_count), N);
  const D = Math.min(whole(run.done_count), N - F);
  const U = N - D - F;
  const status = run.status;
  const errorMessage = status === "failed" && run.error_message ? run.error_message : null;
  const base = { rawStatus: status, errorMessage, counts: { N, D, F, U } };

  if (N === 0) {
    return { ...base, label: "No scans matched", tone: "empty", tooltip: "The run's target had no scans, so nothing was sent." };
  }
  if (D + F === N) {
    return F === 0
      ? { ...base, label: `Finished · ${D} succeeded`, tone: "succeeded", tooltip: "Every scan has a recorded outcome." }
      : {
          ...base,
          label: `Finished · ${D} succeeded · ${F} failed`,
          tone: "finished-with-failures",
          tooltip: "Every scan has a recorded outcome; some failed.",
        };
  }
  if (status === "failed") {
    return {
      ...base,
      label: `Failed · ${D} succeeded · ${F} failed · ${U} without a result`,
      tone: "failed",
      tooltip: "The run failed before every scan had an outcome.",
    };
  }
  if (status === "complete") {
    return {
      ...base,
      label: `Ended · ${D} succeeded · ${F} failed · ${U} without a result`,
      tone: "ended",
      tooltip: "The cluster reports the run is over, but some scans have no outcome.",
    };
  }
  if (status === "partial") {
    return {
      ...base,
      label: `Partial · ${D} / ${N} succeeded · ${F} failed`,
      tone: "partial",
      tooltip: "Some batches failed, at dispatch or on the cluster; the rest of the run may still be processing.",
    };
  }
  const progress = `${D} / ${N} succeeded${F > 0 ? ` · ${F} failed` : ""}`;
  const stage = STAGES[status];
  if (stage) {
    return { ...base, label: `${stage.label} · ${progress}`, tone: "active", tooltip: stage.tooltip };
  }
  return {
    ...base,
    label: `${status} · ${D} / ${N} succeeded`,
    tone: "unknown",
    tooltip: `The run reports an unrecognised status, "${status}".`,
  };
}

export interface ScanStatusLabel {
  label: string;
  raw: string;
}

const SCAN_LABELS: Record<string, string> = {
  queued: "Waiting",
  predicted: "Waiting",
  written: "Result recorded",
  reused: "Result recorded",
  failed: "Failed",
};

export function scanStatusLabel(status: string): ScanStatusLabel {
  return { label: SCAN_LABELS[status] ?? status, raw: status };
}
