/**
 * Server-side proxy for checking an S3 folder of FASTQs before a Cell Ranger run.
 *
 * Forwards `{fastq_url}` with the signed-in user's Supabase token to the workflows service
 * (`POST /scrna/cellranger/folder-check`), which lists the folder and checks its FASTQs
 * without starting anything. The same request checks as starting a run apply. 422 and 429
 * details are passed through: they say what's wrong with the folder, or to wait.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { isJsonMediaType, isSameOrigin } from "@/lib/cyl-pipeline/trigger-proxy";
import { isFolderCheck } from "@/lib/s3-folder";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// One S3 listing upstream; the form waits for it, so this stays short.
const UPSTREAM_TIMEOUT_MS = 20_000;

const DETAIL_PASSTHROUGH_STATUSES = new Set([422, 429]);

function callerSafeDetail(status: number, parsed: unknown): string | null {
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return null;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

export async function POST(request: Request) {
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
    return NextResponse.json({ detail: "The request body must be JSON." }, { status: 400 });
  }
  const { fastq_url } = (body ?? {}) as { fastq_url?: unknown };
  if (typeof fastq_url !== "string" || !fastq_url.trim()) {
    return NextResponse.json({ detail: "Enter an S3 folder." }, { status: 400 });
  }

  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json({ detail: "Sign in to check a folder." }, { status: 401 });
  }

  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

  let upstream: Response;
  try {
    upstream = await fetch(`${workflowsUrl}/scrna/cellranger/folder-check`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ fastq_url }),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
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
  if (!isFolderCheck(parsed)) {
    return NextResponse.json(
      { detail: "Unexpected response from the job service." },
      { status: 502 }
    );
  }
  return NextResponse.json(parsed, { status: 200 });
}
