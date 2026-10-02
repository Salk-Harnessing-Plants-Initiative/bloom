/**
 * Server-side proxy for starting a sleap-roots pipeline run (design D1).
 *
 * Upstream is the workflows service's `POST /pipeline`, reachable in-cluster at
 * `workflows:5100`. Like the video route, this is not a security boundary:
 * Caddy also publishes the service at `/workflows/pipeline`, and the token
 * check and per-user rate limit live upstream (`require_supabase_user`,
 * `enforce_rate_limit`). What this route adds is one place for the CSRF checks,
 * local validation, error normalisation and the timeout.
 *
 * Checks run in this order, before the handler reads the body or contacts
 * upstream (Next has already buffered the body; see trigger-proxy.ts):
 *  0. Starting runs is switched on (503 otherwise; trigger-enabled.ts). Off in
 *     prod until prod's pipeline credential and directories exist (bloom#863).
 *  1. Media type `application/json` (415). A no-cors form post cannot send it,
 *     and a cross-site fetch that does is preflighted, which nothing here
 *     answers with `Access-Control-Allow-*`.
 *  2. Same origin (403). `Origin` is compared with the first `x-forwarded-host`
 *     value, else `Host`. This relies on Caddy having no `trusted_proxies`:
 *     Caddy then replaces any client-sent `X-Forwarded-Host` with the host the
 *     request came in on, and in production browsers reach bloom-web only
 *     through Caddy. A cross-site page cannot set that header without the
 *     preflight above, so adding `trusted_proxies` (e.g. for cloudflared,
 *     bloom#616) does not open a CSRF hole by itself, but revisit this check
 *     when it happens: in trusted mode Caddy forwards the last incoming
 *     `X-Forwarded-Host` header line unchanged (itself possibly a comma
 *     list), while this reads the first comma-separated value.
 *  3. A session with an access token (401).
 *
 * The forwarded body is rebuilt with `params: {}` (see trigger-request.ts).
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { isPipelineTriggerEnabled } from "@/lib/cyl-pipeline/trigger-enabled";
import { parseTriggerRequest } from "@/lib/cyl-pipeline/trigger-request";
import {
  TRIGGER_BODY_MAX_BYTES,
  detailResponse,
  forwardToTrigger,
  isJsonMediaType,
  isSameOrigin,
  readBodyCapped,
} from "@/lib/cyl-pipeline/trigger-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request): Promise<NextResponse> {
  if (!isPipelineTriggerEnabled()) {
    return detailResponse(503, "Starting pipeline runs from Bloom is not enabled in this environment.");
  }
  if (!isJsonMediaType(request.headers)) {
    return detailResponse(415, "Content-Type must be application/json.");
  }
  if (!isSameOrigin(request.headers)) {
    return detailResponse(
      403,
      "This request did not come from a Bloom page. Reload Bloom and try again; if it keeps happening, contact an admin."
    );
  }
  // A short-circuit for the signed-out case; the token itself is verified
  // upstream against Supabase.
  const session = await getSession();
  if (!session?.access_token) {
    return detailResponse(401, "Sign in to start a pipeline run.");
  }

  const read = await readBodyCapped(request, TRIGGER_BODY_MAX_BYTES);
  if (!read.ok) {
    return read.reason === "too-large"
      ? detailResponse(413, `The request body is larger than ${TRIGGER_BODY_MAX_BYTES / 1024} KB.`)
      : detailResponse(422, "The request body is not valid UTF-8.");
  }
  let raw: unknown;
  try {
    raw = JSON.parse(read.text);
  } catch {
    return detailResponse(422, "The request body is not valid JSON.");
  }
  const parsed = parseTriggerRequest(raw);
  if (!parsed.ok) return detailResponse(422, parsed.detail);

  // Read per request, not at module load, so one image works in any environment.
  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";
  return forwardToTrigger(workflowsUrl, session.access_token, parsed.body);
}
