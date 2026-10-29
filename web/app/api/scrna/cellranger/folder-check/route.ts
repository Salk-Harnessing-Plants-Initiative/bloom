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
import { forwardToWorkflows, readJsonBody } from "@/lib/scrna-workflows-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// One folder listing upstream, up to two S3 requests after a region redirect; the form
// waits for it, so this only just outlasts the service's own S3 timeouts.
const UPSTREAM_TIMEOUT_MS = 30_000;

const DETAIL_PASSTHROUGH_STATUSES = new Set([422, 429]);

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

  const read = await readJsonBody(request);
  if (!read.ok) return read.response;
  const { fastq_url } = (read.body ?? {}) as { fastq_url?: unknown };
  if (typeof fastq_url !== "string" || !fastq_url.trim()) {
    return NextResponse.json({ detail: "Enter an S3 folder." }, { status: 400 });
  }

  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json({ detail: "Sign in to check a folder." }, { status: 401 });
  }

  return forwardToWorkflows({
    request,
    path: "/scrna/cellranger/folder-check",
    token: session.access_token,
    payload: { fastq_url },
    timeoutMs: UPSTREAM_TIMEOUT_MS,
    passthrough: DETAIL_PASSTHROUGH_STATUSES,
    isExpected: isFolderCheck,
    okStatus: 200,
  });
}
