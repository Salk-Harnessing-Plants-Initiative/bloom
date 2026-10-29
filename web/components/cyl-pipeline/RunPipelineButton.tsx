"use client";

/**
 * A run action (spec: "Run actions are offered on scan, experiment, wave,
 * accession and drill-down surfaces"). It opens the shared confirm dialog;
 * the dialog is what submits. A `scan_ids` action over MAX_TRIGGER_SCAN_IDS is
 * disabled and says why. A started run is announced to the page, so the
 * experiment page's runs panel shows it at once.
 *
 * With `hidden`, the action is withdrawn but an open dialog stays: a parent
 * whose offer changes live (the drill-down's re-run actions) keeps rendering
 * the button, so its dialog isn't unmounted mid-request.
 */

import { useState, type ReactNode } from "react";
import { MAX_TRIGGER_SCAN_IDS, scanIdsOverLimitText } from "@/lib/cyl-pipeline/trigger-request";
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
  /** Withdraw the action; an open dialog stays until it is closed. */
  hidden?: boolean;
}

export function RunPipelineButton({ target, label, title, note, hidden = false }: RunPipelineButtonProps) {
  // The target as it was when the dialog opened; null while closed.
  const [opened, setOpened] = useState<TriggerTarget | null>(null);
  const announce = useAnnounceStartedRun();
  const overLimit = target.target_level === "scan_ids" && target.scan_ids.length > MAX_TRIGGER_SCAN_IDS;

  // The dialog keeps one place in the tree whether or not the action is hidden, so hiding never remounts it.
  return (
    <span className={hidden ? "hidden" : "inline-flex flex-col items-start gap-1"}>
      {!hidden && (
        <>
          <button
            type="button"
            onClick={() => setOpened(target)}
            disabled={overLimit}
            className="rounded-md border border-lime-700 px-2.5 py-1 text-sm text-lime-700 hover:bg-lime-50 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400"
          >
            {label}
          </button>
          {overLimit && target.target_level === "scan_ids" && (
            <span className="text-xs text-stone-500">{scanIdsOverLimitText(target.scan_ids.length)}</span>
          )}
          {note && <span className="max-w-prose text-xs text-amber-800">{note}</span>}
        </>
      )}
      {opened && <RunPipelineDialog target={opened} title={title} onClose={() => setOpened(null)} onStarted={announce} />}
    </span>
  );
}
