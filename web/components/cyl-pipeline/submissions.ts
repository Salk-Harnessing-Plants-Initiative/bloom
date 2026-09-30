"use client";

/**
 * Which run targets this browser tab has already submitted, and how each
 * submission went (spec: "Confirm dialog submits once and reports outcomes
 * without inviting duplicate runs").
 *
 * It lives at module level, not in the dialog, because a dialog can be closed
 * mid-request, closed after a "may have started" answer, or unmounted by its
 * page (a drill-down's re-run action is withdrawn by a live event). With the
 * state inside the dialog, reopening would offer a fresh confirm while the
 * first request might still start a run. Here, a target that is sending,
 * started or uncertain stays so until the page is reloaded; only a refusal
 * (nothing started) clears it.
 */

import { useSyncExternalStore } from "react";
import type { TriggerTarget } from "@/lib/cyl-pipeline/trigger-target";

export type Submission =
  | { kind: "sending" }
  | { kind: "started"; runId: number; scanCount: number }
  /** The run may or may not exist (502, 504, other 5xx, lost connection, malformed success). */
  | { kind: "uncertain" };

const submissions = new Map<string, Submission>();
const listeners = new Set<() => void>();

function changed(): void {
  listeners.forEach((l) => l());
}

/** One key per target: a selection is named by its sorted, distinct ids. */
export function submissionKey(target: TriggerTarget): string {
  if (target.target_level === "scan_ids") {
    return `scan_ids:${[...new Set(target.scan_ids)].sort((a, b) => a - b).join(",")}`;
  }
  return `${target.target_level}:${target.target_id}`;
}

/** Mark `key` as sending, unless it already has a submission. Synchronous, so it is the double-click guard. */
export function beginSubmission(key: string): boolean {
  if (submissions.has(key)) return false;
  submissions.set(key, { kind: "sending" });
  changed();
  return true;
}

/** Record how a submission ended; null (a refusal: nothing started) allows another try. */
export function settleSubmission(key: string, result: Submission | null): void {
  if (result) submissions.set(key, result);
  else submissions.delete(key);
  changed();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

export function useSubmission(key: string): Submission | undefined {
  return useSyncExternalStore(
    subscribe,
    () => submissions.get(key),
    () => undefined,
  );
}

/** For tests: forget every submission. */
export function resetSubmissions(): void {
  submissions.clear();
  changed();
}
