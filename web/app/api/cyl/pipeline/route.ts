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
 * Checks run in this order, before the body is read or upstream contacted:
 *  1. Media type `application/json` (415). A no-cors form post cannot send it,
 *     and a CORS fetch that could is preflighted, which nothing here answers
 *     with `Access-Control-Allow-*`.
 *  2. Same origin (403). `Origin` is compared with the first `x-forwarded-host`
 *     value, else `Host`. This depends on Caddy having no `trusted_proxies`:
 *     without it, Caddy replaces any client-sent `X-Forwarded-Host` with the
 *     real host, and only Caddy can reach bloom-web. If a proxy that Caddy
 *     trusts (e.g. cloudflared, bloom#616) ever fronts it, that proxy's value
 *     is the one compared, and it must be set by that proxy, not the client.
 *  3. A session with an access token (401).
 *
 * The forwarded body is rebuilt with `params: {}` (see trigger-request.ts).
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { parseTriggerRequest } from "@/lib/cyl-pipeline/trigger-request";
import {
  TRIGGER_BODY_MAX_BYTES,
  detail,
  isJsonMediaType,
  isSameOrigin,
  mapUpstreamFailure,
  mapUpstreamResponse,
  readBodyCapped,
} from "@/lib/cyl-pipeline/trigger-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Under undici's 300 s header timeout. A 5000-scan trigger makes 200
// sequential enqueue calls before it answers.
const UPSTREAM_TIMEOUT_MS = 120_000;

export async function POST(request: Request): Promise<NextResponse> {
  if (!isJsonMediaType(request.headers)) {
    return detail(415, "Content-Type must be application/json.");
  }
  if (!isSameOrigin(request.headers)) {
    return detail(403, "Cross-origin requests are not allowed.");
  }
  // A short-circuit for the signed-out case; the token itself is verified
  // upstream against Supabase.
  const session = await getSession();
  if (!session?.access_token) {
    return detail(401, "Sign in to start a pipeline run.");
  }

  const read = await readBodyCapped(request, TRIGGER_BODY_MAX_BYTES);
  if (!read.ok) {
    return detail(413, "The request body is larger than 256 KB.");
  }
  let raw: unknown;
  try {
    raw = JSON.parse(read.text);
  } catch {
    return detail(422, "The request body is not valid JSON.");
  }
  const parsed = parseTriggerRequest(raw);
  if (!parsed.ok) return detail(422, parsed.detail);

  // Read per request, not at module load, so one image works in any environment.
  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

  let upstream: Response;
  try {
    upstream = await fetch(`${workflowsUrl}/pipeline`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(parsed.body),
      // A redirect is not an answer this route knows; it maps to 502.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (err) {
    return mapUpstreamFailure(err);
  }
  return mapUpstreamResponse(upstream);
}
