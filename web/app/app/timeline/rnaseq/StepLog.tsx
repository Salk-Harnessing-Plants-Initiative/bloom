"use client";

import { useState } from "react";
import type { StepId } from "@/lib/rnaseq-runs";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "shown"; log: string; truncated: boolean }
  | { kind: "message"; text: string };

/** What to say when a log can't be shown, by the proxy's status. */
export function logMessage(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "Sign in to see logs.";
  if (status === 503 || status === 502) return "Logs aren't available yet.";
  return "Couldn't load this log. Try again shortly.";
}

/** One step's log, loaded on request, with Refresh while the step runs. */
export default function StepLog({
  runId,
  step,
  running,
}: {
  runId: number;
  step: StepId;
  running: boolean;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });

  async function load() {
    setState({ kind: "loading" });
    try {
      const res = await fetch(
        `/api/scrna/cellranger/runs/${runId}/logs?step=${encodeURIComponent(step)}`
      );
      const body = await res.json().catch(() => null);
      if (res.ok && typeof body?.log === "string") {
        setState({ kind: "shown", log: body.log, truncated: Boolean(body.truncated) });
      } else {
        setState({ kind: "message", text: logMessage(res.status, body?.detail) });
      }
    } catch {
      setState({ kind: "message", text: "Couldn't reach Bloom. Try again shortly." });
    }
  }

  if (state.kind === "idle") {
    return (
      <button type="button" onClick={load} className="text-sm text-lime-700 underline-offset-4 hover:underline">
        View log
      </button>
    );
  }

  return (
    <div className="mt-2 w-full">
      <div className="mb-1 flex items-center gap-4">
        <button
          type="button"
          onClick={load}
          disabled={state.kind === "loading"}
          className="text-sm text-lime-700 underline-offset-4 hover:underline disabled:opacity-50"
        >
          {state.kind === "loading" ? "Loading…" : running ? "Refresh" : "Reload"}
        </button>
        <button
          type="button"
          onClick={() => setState({ kind: "idle" })}
          className="text-sm text-stone-500 hover:text-stone-800"
        >
          Hide
        </button>
      </div>
      {state.kind === "message" ? (
        <p role="status" className="text-sm text-stone-600">
          {state.text}
        </p>
      ) : state.kind === "shown" ? (
        <>
          {state.truncated ? (
            <p className="mb-1 text-xs text-stone-500">Showing the end of the log; it may be cut.</p>
          ) : null}
          <pre className="max-h-96 overflow-auto rounded-md bg-stone-900 p-3 text-xs leading-relaxed text-stone-100">
            {state.log || "The log is empty."}
          </pre>
        </>
      ) : null}
    </div>
  );
}
