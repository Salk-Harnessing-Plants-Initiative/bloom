"use client";

/**
 * A run action (spec: "Run actions are offered on scan, experiment, wave,
 * accession and drill-down surfaces"). It opens the shared confirm dialog;
 * the dialog is what submits. A `scan_ids` action over MAX_TRIGGER_SCAN_IDS is
 * disabled and says why. A started run is announced to the page, so the
 * experiment page's runs panel shows it at once.
 */

import { useState, type ReactNode } from "react";
import { MAX_TRIGGER_SCAN_IDS } from "@/lib/cyl-pipeline/trigger-request";
import type { TriggerTarget } from "@/lib/cyl-pipeline/trigger-target";
import { RunPipelineDialog } from "./RunPipelineDialog";
import { useAnnounceStartedRun } from "./started-runs";

export interface RunPipelineButtonProps {
  target: TriggerTarget;
  /** The button's text, e.g. "Run experiment". */
  label: string;
  /** What the dialog's headline calls the target. */
  title: string;
  /** Shown beside the button, e.g. a re-run warning. */
  note?: ReactNode;
}

export function overLimitText(count: number): string {
  return `${count} scans selected; one run of selected scans takes at most ${MAX_TRIGGER_SCAN_IDS}.`;
}

export function RunPipelineButton({ target, label, title, note }: RunPipelineButtonProps) {
  const [open, setOpen] = useState(false);
  const announce = useAnnounceStartedRun();
  const overLimit = target.target_level === "scan_ids" && target.scan_ids.length > MAX_TRIGGER_SCAN_IDS;

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={overLimit}
        className="rounded-md border border-lime-700 px-2.5 py-1 text-sm text-lime-700 hover:bg-lime-50 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400"
      >
        {label}
      </button>
      {overLimit && target.target_level === "scan_ids" && <span className="text-xs text-stone-500">{overLimitText(target.scan_ids.length)}</span>}
      {note && <span className="max-w-prose text-xs text-amber-800">{note}</span>}
      {open && <RunPipelineDialog target={target} title={title} onClose={() => setOpen(false)} onStarted={announce} />}
    </span>
  );
}
