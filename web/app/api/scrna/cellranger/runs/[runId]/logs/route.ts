/**
 * Server-side proxy for the end of one step's log of a Cell Ranger run.
 *
 * Forwards the signed-in user's Supabase token to the workflows service
 * (`GET /scrna/cellranger/runs/{runId}/logs?step=`). The run id and step are checked
 * here first, so only a known step of a numeric run reaches upstream. The 404, 409 and
 * 410 details are written for scientists and passed through; other details are not.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { CELLRANGER_STEPS } from "@/lib/rnaseq-runs";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const UPSTREAM_TIMEOUT_MS = 20_000;

const DETAIL_PASSTHROUGH_STATUSES = new Set([404, 409, 410]);

const STEPS: ReadonlySet<string> = new Set(CELLRANGER_STEPS.map((s) => s.id));

export type StepLog = { step: string; log: string; truncated: boolean };

function detail(status: number, parsed: unknown): string | null {
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return null;
  const value = (parsed as { detail?: unknown } | null)?.detail;
  return typeof value === "string" && value.trim() ? value : null;
}

function isStepLog(value: unknown): value is StepLog {
  const v = value as Partial<StepLog> | null;
  return typeof v?.step === "string" && typeof v.log === "string" && typeof v.truncated === "boolean";
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  const { runId } = await params;
  if (!/^[1-9][0-9]{0,17}$/.test(runId)) {
    return NextResponse.json({ detail: "The run id must be a positive integer." }, { status: 400 });
  }
  const step = new URL(request.url).searchParams.get("step") ?? "";
  if (!STEPS.has(step)) {
    return NextResponse.json({ detail: "Unknown step." }, { status: 400 });
  }

  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json({ detail: "Sign in to see logs." }, { status: 401 });
  }

  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";
  let upstream: Response;
  try {
    upstream = await fetch(
      `${workflowsUrl}/scrna/cellranger/runs/${runId}/logs?step=${encodeURIComponent(step)}`,
      {
        headers: { Authorization: `Bearer ${session.access_token}` },
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      }
    );
  } catch {
    return NextResponse.json({ detail: null }, { status: 502 });
  }

  const parsed: unknown = await upstream.json().catch(() => null);
  if (!upstream.ok) {
    return NextResponse.json({ detail: detail(upstream.status, parsed) }, { status: upstream.status });
  }
  if (!isStepLog(parsed)) {
    return NextResponse.json({ detail: null }, { status: 502 });
  }
  const { log, truncated } = parsed;
  return NextResponse.json({ step: parsed.step, log, truncated } satisfies StepLog);
}
