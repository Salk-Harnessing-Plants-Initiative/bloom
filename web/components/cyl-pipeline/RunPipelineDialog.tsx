"use client";

/**
 * The one confirm dialog every run action opens (spec: "Confirm dialog shows
 * read-only resolved params and a pre-check, without predicting skips" and
 * "Confirm dialog submits once and reports outcomes without inviting
 * duplicate runs"; design D4).
 *
 * - The target is fixed when the dialog opens: what it checked is what it
 *   sends, even if the caller's target changes meanwhile (a drill-down's
 *   failed rows change live).
 * - It enumerates that target with the trigger's own filters, so N is the
 *   trigger's count, then reads the pre-check (K, L) and the concurrent runs.
 *   Confirm stays disabled until all of them have settled.
 * - It predicts no skips: the trigger enqueues every scan, and skipping is
 *   decided per stage on the cluster; a server-side preview is #898. The
 *   resolved params are display only, and no parameter hash is computed.
 * - It submits once per target (submissions.ts). The trigger has no
 *   idempotency key and isn't transactional (design D1), so a 502, a 504,
 *   any other 5xx or a lost connection may still have started a run: those
 *   say so, point at the runs list, and never offer confirm again, even after
 *   the dialog is closed and reopened. Only answers that prove nothing started
 *   (429, 401, 404, 422 and the proxy's own refusals) allow another try.
 */

import Dialog from "@mui/material/Dialog";
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { formatElapsed } from "@/lib/cyl-pipeline/elapsed";
import { paramsSummary } from "@/lib/cyl-pipeline/params-summary";
import { fetchConcurrentRuns, fetchLatestSources, fetchTargetScans, type ConcurrentRuns } from "@/lib/cyl-pipeline/queries";
import { runDisplay } from "@/lib/cyl-pipeline/run-display";
import { requesterText } from "@/lib/cyl-pipeline/run-text";
import type { ScanMeta } from "@/lib/cyl-pipeline/scan-meta";
import { isTriggerResult, MAX_TRIGGER_SCAN_IDS, scanIdsOverLimitText } from "@/lib/cyl-pipeline/trigger-request";
import type { TriggerTarget } from "@/lib/cyl-pipeline/trigger-target";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { StartedRun } from "./started-runs";
import { beginSubmission, settleSubmission, submissionKey, useSubmission, type Submission } from "./submissions";

export const TRIGGER_URL = "/api/cyl/pipeline";
/** At or above this many scans, confirm waits for an acknowledgement: runs can't be cancelled from Bloom. */
export const LARGE_RUN_SCANS = 500;
const PARAM_GROUPS_SHOWN = 3;
const MISSING_IDS_SHOWN = 20;
const OVERRIDES_ISSUE = "https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/897";

const SUCCESS_TIMING_NOTE =
  "Results arrive when each batch of up to 25 scans finishes; counts often stay at 0 for most of the run. Reload the traits page to see new results.";
const RATE_LIMITED =
  "Too many requests — this limit is shared with other workflow actions, such as video generation and Cell Ranger runs. Try again in about a minute.";
const SESSION_EXPIRED = "Your session expired — sign in again.";
const REFUSED = "The pipeline service refused this request.";

export interface RunPipelineDialogProps {
  target: TriggerTarget;
  /** What the headline calls the target, e.g. "experiment Exp five". */
  title: string;
  onClose: () => void;
  onStarted?: (run: StartedRun) => void;
}

interface Checked {
  scans: ScanMeta[];
  latest: Map<number, number | null>;
  concurrent: ConcurrentRuns;
  userId: string | null;
}

type Load = { state: "loading" } | { state: "failed"; message: string } | ({ state: "ready" } & Checked);

/** What one POST settled as; a refusal is shown here, and every other outcome is kept in submissions.ts. */
type Settled = Submission | { kind: "refused"; message: string };

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The body the proxy validates; it adds `params: {}` itself. */
function requestBody(target: TriggerTarget) {
  return target.target_level === "scan_ids"
    ? { target_level: target.target_level, scan_ids: target.scan_ids }
    : { target_level: target.target_level, target_id: target.target_id };
}

async function send(target: TriggerTarget): Promise<Settled> {
  let res: Response;
  try {
    res = await fetch(TRIGGER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody(target)),
    });
  } catch {
    // The request may have reached the trigger before the connection failed.
    return { kind: "uncertain" };
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // Treated as no body below.
  }
  if (res.ok) {
    return isTriggerResult(body) ? { kind: "started", runId: body.pipeline_run_id, scanCount: body.scan_count } : { kind: "uncertain" };
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  const text = typeof detail === "string" && detail ? detail : null;
  if (res.status === 429) return { kind: "refused", message: RATE_LIMITED };
  if (res.status === 401) return { kind: "refused", message: SESSION_EXPIRED };
  // Any 5xx, including the proxy's 502 and 504, may follow a partial trigger.
  if (res.status >= 500) return { kind: "uncertain" };
  // 404 and 422 from the trigger, and the proxy's own 403/413/415/422, all come before any insert.
  return { kind: "refused", message: text ?? REFUSED };
}

interface Content {
  N: number;
  blockers: string[];
  stageInCount: number;
  K: number;
  L: number;
  groups: ReturnType<typeof paramsSummary>["groups"];
}

function content(target: TriggerTarget, { scans, latest }: Checked): Content {
  const N = scans.length;
  const blockers: string[] = [];
  if (N === 0) blockers.push("No scans to run");
  if (target.target_level === "scan_ids") {
    const found = new Set(scans.map((s) => s.scan_id));
    const missing = [...new Set(target.scan_ids)].filter((id) => !found.has(id));
    if (missing.length > 0) {
      const listed = missing.slice(0, MISSING_IDS_SHOWN).join(", ");
      const rest = missing.length > MISSING_IDS_SHOWN ? ` and ${missing.length - MISSING_IDS_SHOWN} more` : "";
      blockers.push(
        `Selected but not found: ${missing.length === 1 ? "scan" : "scans"} ${listed}${rest}. The pipeline refuses a run that includes scans it can't find.`,
      );
    }
    if (N > MAX_TRIGGER_SCAN_IDS) blockers.push(scanIdsOverLimitText(N));
  }
  let K = 0;
  let L = 0;
  for (const { scan_id } of scans) {
    if (!latest.has(scan_id)) continue;
    if (latest.get(scan_id) != null) K += 1;
    else L += 1;
  }
  const { groups, stageInCount } = paramsSummary(scans);
  return { N, blockers, stageInCount, K, L, groups };
}

export function RunPipelineDialog({ target: requested, title, onClose, onStarted }: RunPipelineDialogProps) {
  const headingId = useId();
  const now = useNow();
  // Fixed at open: later changes to the caller's target don't reach what is checked or sent.
  const [target] = useState(requested);
  const [key] = useState(() => submissionKey(requested));
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [acknowledged, setAcknowledged] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const submission = useSubmission(key);

  useEffect(() => {
    let active = true;
    const client = createClientSupabaseClient();
    (async () => {
      const scans = await fetchTargetScans(client, target);
      const experimentIds = [...new Set(scans.flatMap((s) => (s.experiment_id == null ? [] : [s.experiment_id])))];
      const [latest, concurrent, userId] = await Promise.all([
        fetchLatestSources(client, scans.map((s) => s.scan_id)),
        fetchConcurrentRuns(client, experimentIds),
        client.auth
          .getSession()
          .then(({ data }) => data.session?.user?.id ?? null)
          .catch(() => null),
      ]);
      if (active) setLoad({ state: "ready", scans, latest, concurrent, userId });
    })().catch((e: unknown) => {
      if (active) setLoad({ state: "failed", message: message(e) });
    });
    return () => {
      active = false;
    };
  }, [target]);

  const ready = load.state === "ready" ? load : null;
  const c = ready ? content(target, ready) : null;
  const needsAck = c !== null && c.N >= LARGE_RUN_SCANS;
  const canConfirm = c !== null && c.blockers.length === 0 && (!needsAck || acknowledged) && submission === undefined;

  const submit = async () => {
    if (!canConfirm || !ready) return;
    // Synchronous, and shared by every dialog for this target: the double-click guard.
    if (!beginSubmission(key)) return;
    setRefusal(null);
    const result = await send(target);
    if (result.kind === "refused") {
      settleSubmission(key, null);
      setRefusal(result.message);
      return;
    }
    settleSubmission(key, result);
    if (result.kind === "started") {
      onStarted?.({
        pipeline_run_id: result.runId,
        scan_count: result.scanCount,
        target,
        requested_by: ready.userId,
        started_at: new Date().toISOString(),
      });
    }
  };

  const sent = submission !== undefined;
  const item = (g: Content["groups"][number]) => <li key={`${g.species}|${g.age}`}>{`${g.species} · ${g.mode} · ${g.age} — ${g.count}`}</li>;

  return (
    <Dialog open onClose={onClose} aria-labelledby={headingId} maxWidth="sm" fullWidth>
      <div className="space-y-4 p-6 text-sm text-stone-700">
        <h2 id={headingId} className="text-lg text-stone-900">
          Run the pipeline on {title}
          <span className="text-stone-500"> · {c ? plural(c.N, "scan") : "counting scans…"}</span>
        </h2>

        {load.state === "failed" && (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800">
            Could not check this run&apos;s scans: {load.message}. Close and try again.
          </div>
        )}
        {load.state === "loading" && <p className="text-stone-500">Checking the scans and any unfinished runs…</p>}

        {c && ready && (
          <>
            {c.blockers.length > 0 && (
              <ul data-testid="blockers" className="space-y-1 rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
                {c.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}

            {c.stageInCount > 0 && (
              <p className="text-amber-800">
                {plural(c.stageInCount, "scan")} will fail at stage-in — ask a Bloom admin to fix the plant metadata
              </p>
            )}

            {ready.concurrent.runs.length > 0 && (
              <section data-testid="concurrent-runs">
                <h3 className="font-medium text-stone-900">Unfinished runs touching the same experiment</h3>
                <p className="text-stone-500">They may still be processing some of these scans.</p>
                <ul className="mt-1 space-y-1">
                  {ready.concurrent.runs.map((run) => (
                    <li key={run.id} data-testid={`concurrent-run-${run.id}`}>
                      <Link href={`/app/cyl-pipeline-runs/${run.id}`} className="text-lime-700 hover:underline">
                        Run {run.id}
                      </Link>
                      {` · ${requesterText(run.requested_by, ready.userId)} · ${runDisplay(run).label}`}
                      {now !== null && ` · started ${formatElapsed(run.created_at, now)} ago`}
                    </li>
                  ))}
                </ul>
                {ready.concurrent.more > 0 && <p className="text-stone-500">and {ready.concurrent.more} more</p>}
              </section>
            )}

            {c.N > 0 &&
              (c.K === c.N ? (
                <p>
                  All {c.N} scans already have pipeline results. The run will still be created and sent to the cluster, which
                  skips scans it has already processed with the same models and code.
                </p>
              ) : (
                <div>
                  <p>
                    {c.K} of {c.N} already have pipeline results.
                  </p>
                  <details data-testid="precheck-details" className="text-stone-600">
                    <summary className="cursor-pointer text-stone-500">Details</summary>
                    <p className="mt-1">
                      {c.L > 0 &&
                        `${c.L} more scans have only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. `}
                      {`All ${c.N} will be sent; the cluster may skip work for scans it has already processed with the same images, parameters, models and code.`}
                    </p>
                  </details>
                </div>
              ))}

            {c.N > 0 && (
              <section data-testid="params">
                <h3 className="font-medium text-stone-900">Parameters</h3>
                <ul className="mt-1">{c.groups.slice(0, PARAM_GROUPS_SHOWN).map(item)}</ul>
                {c.groups.length > PARAM_GROUPS_SHOWN && (
                  <details>
                    <summary className="cursor-pointer text-stone-500">
                      {plural(c.groups.length - PARAM_GROUPS_SHOWN, "more parameter set")}
                    </summary>
                    <ul>{c.groups.slice(PARAM_GROUPS_SHOWN).map(item)}</ul>
                  </details>
                )}
                <p className="mt-1 text-xs text-stone-500">
                  Parameters come from each scan&apos;s metadata; overrides aren&apos;t supported yet (
                  <a href={OVERRIDES_ISSUE} target="_blank" rel="noreferrer" className="text-lime-700 underline hover:no-underline">
                    bloom#897
                  </a>
                  ).
                </p>
              </section>
            )}

            {needsAck && (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  disabled={sent}
                />
                <span>I understand this queues {c.N} scans on the shared GPU cluster; runs can&apos;t be cancelled from Bloom.</span>
              </label>
            )}
          </>
        )}

        {submission?.kind === "sending" && (
          <p role="status" className="text-stone-600">
            Starting the run. Large runs can take up to two minutes. You can close this; the run keeps starting, and reopening
            this dialog shows the result.
          </p>
        )}
        {submission?.kind === "started" && (
          <div role="status" className="space-y-1 rounded-md border border-lime-200 bg-lime-50 p-3 text-lime-900">
            <p>
              Run {submission.runId} started with {plural(submission.scanCount, "scan")}.
            </p>
            {c && submission.scanCount !== c.N && (
              <p>
                The pipeline service counted {submission.scanCount} scans; this dialog counted {c.N}. The target&apos;s scans may
                have changed in between.
              </p>
            )}
            <p>{SUCCESS_TIMING_NOTE}</p>
            <Link href={`/app/cyl-pipeline-runs/${submission.runId}`} className="text-lime-700 underline hover:no-underline">
              Open run {submission.runId}
            </Link>
          </div>
        )}
        {submission?.kind === "uncertain" && (
          <div role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
            The run may have started, but Bloom couldn&apos;t confirm it. Check{" "}
            <Link href="/app/cyl-pipeline-runs" className="underline hover:no-underline">
              Cylinder pipeline runs
            </Link>{" "}
            before trying again.
          </div>
        )}
        {refusal !== null && !sent && (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800">
            {refusal}
          </div>
        )}

        <div className="flex justify-end gap-3 pt-2">
          {/* Focused on open, so Escape and Tab start inside the dialog. */}
          <button type="button" autoFocus onClick={onClose} className="rounded-md px-3 py-1.5 text-stone-600 hover:bg-stone-100">
            {sent ? "Close" : "Cancel"}
          </button>
          {submission?.kind !== "started" && (
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!canConfirm}
              className="rounded-md bg-lime-700 px-3 py-1.5 text-white hover:bg-lime-800 disabled:cursor-not-allowed disabled:bg-stone-300"
            >
              {submission?.kind === "sending" ? "Starting…" : "Start run"}
            </button>
          )}
        </div>
      </div>
    </Dialog>
  );
}
