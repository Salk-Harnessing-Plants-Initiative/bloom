/**
 * Server-side proxy for starting a Cell Ranger run.
 *
 * Forwards `{sample or fastq_url, reference, metadata?, sra_runs?}` with the signed-in user's Supabase token to the
 * workflows service (`POST /scrna/cellranger/runs`, in-cluster at `workflows:5100`),
 * which checks the names, records the run and queues it. Proxying keeps the token out
 * of client JS. A request must be JSON (415 otherwise) and come from a Bloom page (403
 * otherwise), as for the cylinder pipeline trigger. Only 409, 422 and 429 details are passed
 * through: those say a name is taken, name the rule a value broke, or say to wait; other
 * upstream details are written for operators.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { isJsonMediaType, isSameOrigin } from "@/lib/cyl-pipeline/trigger-proxy";
import { isStartedRun } from "@/lib/scrna-jobs";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Queuing a run is one database call upstream, so this only has to outlast a slow gateway.
const UPSTREAM_TIMEOUT_MS = 30_000;

const DETAIL_PASSTHROUGH_STATUSES = new Set([409, 422, 429]);

function callerSafeDetail(status: number, parsed: unknown): string | null {
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return null;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

export async function POST(request: Request) {
  // The same checks, in the same order, as the cylinder pipeline trigger proxy.
  if (!isJsonMediaType(request.headers)) {
    return NextResponse.json(
      { detail: "Content-Type must be application/json." },
      { status: 415 }
    );
  }
  if (!isSameOrigin(request.headers)) {
    return NextResponse.json(
      { detail: "This request did not come from a Bloom page. Reload Bloom and try again." },
      { status: 403 }
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { detail: "The request body must be JSON." },
      { status: 400 }
    );
  }
  const { sample, fastq_url, reference, metadata, sra_runs } = (body ?? {}) as {
    sample?: unknown;
    fastq_url?: unknown;
    reference?: unknown;
    metadata?: unknown;
    sra_runs?: unknown;
  };
  // A run's reads come from an S3 folder (whose files name the sample) or a named sample.
  const hasFolder = fastq_url !== undefined;
  if (
    typeof reference !== "string" ||
    (hasFolder
      ? typeof fastq_url !== "string" || sample !== undefined
      : typeof sample !== "string")
  ) {
    return NextResponse.json(
      { detail: "Choose the reads (an S3 folder or a sample) and a reference." },
      { status: 400 }
    );
  }
  if (
    metadata !== undefined &&
    (metadata === null || typeof metadata !== "object" || Array.isArray(metadata))
  ) {
    return NextResponse.json(
      { detail: "The dataset details must be a JSON object." },
      { status: 400 }
    );
  }

  // The service checks each run ID; this only keeps the field's shape.
  if (
    sra_runs !== undefined &&
    (!Array.isArray(sra_runs) || !sra_runs.every((run) => typeof run === "string"))
  ) {
    return NextResponse.json(
      { detail: "The SRA run IDs must be a list." },
      { status: 400 }
    );
  }

  // Only a short-circuit for the signed-out case; the service verifies the token itself.
  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json(
      { detail: "Sign in to start a job." },
      { status: 401 }
    );
  }

  // Read per request, so one image works in any environment and tests can vary it.
  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

  let upstream: Response;
  try {
    upstream = await fetch(`${workflowsUrl}/scrna/cellranger/runs`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...(hasFolder ? { fastq_url } : { sample }),
        reference,
        ...(metadata === undefined ? {} : { metadata }),
        ...(sra_runs === undefined ? {} : { sra_runs }),
      }),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Unreachable or timed out; don't leak the internal host into the response.
    return NextResponse.json(
      { detail: "The job service isn't available right now." },
      { status: 502 }
    );
  }

  const text = await upstream.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return NextResponse.json(
      { detail: upstream.ok ? "Unexpected response from the job service." : null },
      { status: upstream.ok ? 502 : upstream.status }
    );
  }

  if (!upstream.ok) {
    return NextResponse.json(
      { detail: callerSafeDetail(upstream.status, parsed) },
      { status: upstream.status }
    );
  }
  if (!isStartedRun(parsed)) {
    return NextResponse.json(
      { detail: "Unexpected response from the job service." },
      { status: 502 }
    );
  }
  return NextResponse.json(parsed, { status: 201 });
}
