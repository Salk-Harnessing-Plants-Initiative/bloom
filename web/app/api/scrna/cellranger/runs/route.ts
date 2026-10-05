/**
 * Server-side proxy for starting a Cell Ranger run.
 *
 * Forwards `{sample or fastq_url and fastq_files, reference, metadata?, sra_runs?}` with the
 * signed-in user's Supabase token to the workflows service (`POST /scrna/cellranger/runs`,
 * in-cluster at `workflows:5100`), which checks the request, records the run and queues
 * it. Proxying keeps the token out of client JS. A request must be JSON (415 otherwise),
 * come from a Bloom page (403 otherwise) and be at most 256 KB (413 otherwise), as for the
 * cylinder pipeline trigger. Only 409, 422 and 429 details are passed through: those say
 * the folder changed since its check or the run already exists, name the rule a value
 * broke, or say to wait; other upstream details are written for operators.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { isJsonMediaType, isSameOrigin } from "@/lib/cyl-pipeline/trigger-proxy";
import { isStartedRun } from "@/lib/scrna-jobs";
import { MAX_FOLDER_FILES, type FolderFile } from "@/lib/s3-folder";
import { forwardToWorkflows, readJsonBody } from "@/lib/scrna-workflows-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// A folder start lists the folder again (up to two S3 requests after a region redirect),
// then makes one database call, so this outlasts the service's own S3 timeouts.
const UPSTREAM_TIMEOUT_MS = 45_000;

const DETAIL_PASSTHROUGH_STATUSES = new Set([409, 422, 429]);

/** The files the folder check showed, keeping only their name, size and ETag, or null. */
function shownFiles(value: unknown): FolderFile[] | null {
  if (!Array.isArray(value) || value.length > MAX_FOLDER_FILES) return null;
  const files: FolderFile[] = [];
  for (const item of value) {
    const f = item as Partial<FolderFile> | null;
    if (
      typeof f?.name !== "string" ||
      typeof f.size !== "number" ||
      typeof f.etag !== "string"
    ) {
      return null;
    }
    files.push({ name: f.name, size: f.size, etag: f.etag });
  }
  return files;
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

  const read = await readJsonBody(request);
  if (!read.ok) return read.response;
  const { sample, fastq_url, fastq_files, reference, metadata, sra_runs } = (read.body ??
    {}) as {
    sample?: unknown;
    fastq_url?: unknown;
    fastq_files?: unknown;
    reference?: unknown;
    metadata?: unknown;
    sra_runs?: unknown;
  };
  // A run's reads come from an S3 folder (whose files name the sample) or a named sample.
  // A folder comes with the files its check showed; the service refuses it if they changed.
  const hasFolder = fastq_url !== undefined;
  const files = hasFolder ? shownFiles(fastq_files) : null;
  if (
    typeof reference !== "string" ||
    (hasFolder
      ? typeof fastq_url !== "string" || sample !== undefined || files === null
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

  return forwardToWorkflows({
    request,
    path: "/scrna/cellranger/runs",
    token: session.access_token,
    payload: {
      ...(hasFolder ? { fastq_url, fastq_files: files } : { sample }),
      reference,
      ...(metadata === undefined ? {} : { metadata }),
      ...(sra_runs === undefined ? {} : { sra_runs }),
    },
    timeoutMs: UPSTREAM_TIMEOUT_MS,
    passthrough: DETAIL_PASSTHROUGH_STATUSES,
    isExpected: isStartedRun,
    okStatus: 201,
  });
}
