"use client";

/**
 * A run's counts-first display state, as every pipeline-run view shows it:
 * the label (its tooltip explains the stage), the raw status as secondary
 * text, and a failed run's error message. With `failedHref`, the "F failed"
 * part of the label links there.
 */

import Link from "next/link";
import { runDisplay, type RunCounts, type RunTone } from "@/lib/cyl-pipeline/run-display";

const TONE: Record<RunTone, string> = {
  empty: "text-stone-500",
  succeeded: "text-lime-700",
  "finished-with-failures": "text-amber-700",
  failed: "text-red-700",
  ended: "text-amber-700",
  partial: "text-amber-700",
  active: "text-sky-700",
  unknown: "text-stone-700",
};

export function RunState({ run, failedHref }: { run: RunCounts; failedHref?: string }) {
  const display = runDisplay(run);
  const parts = display.label.split(" · ");
  return (
    <span className="inline-flex flex-col">
      <span className={`font-medium ${TONE[display.tone]}`} title={display.tooltip}>
        {parts.map((part, i) => (
          <span key={i}>
            {i > 0 && " · "}
            {failedHref && /^\d+ failed$/.test(part) ? (
              <Link href={failedHref} className="underline hover:no-underline">
                {part}
              </Link>
            ) : (
              part
            )}
          </span>
        ))}
      </span>
      <span className="text-xs text-stone-500">{display.rawStatus}</span>
      {display.errorMessage && (
        <span className="mt-1 max-h-24 max-w-prose overflow-auto whitespace-pre-wrap break-words text-xs text-red-700">{display.errorMessage}</span>
      )}
    </span>
  );
}
