/**
 * What the scRNA proxy routes share: reading the caller's JSON body under a size cap, and
 * forwarding one request to the workflows service with the signed-in user's token.
 *
 * Only the statuses a route names pass the service's `detail` through; other upstream
 * details are written for operators. Unreachable, timed out or redirected all read as
 * "isn't available", and a reply the route's guard doesn't recognise is a 502.
 */

import { NextResponse } from "next/server";
import { readBodyCapped, TRIGGER_BODY_MAX_BYTES } from "@/lib/cyl-pipeline/trigger-proxy";

/** Where the service is, read per request so one image works in any environment. */
export function workflowsUrl(): string {
  return process.env.WORKFLOWS_URL ?? "http://workflows:5100";
}

export const UNAVAILABLE = "The job service isn't available right now.";
export const UNEXPECTED = "Unexpected response from the job service.";
// Longest detail passed through; a service refusal never needs more.
export const MAX_DETAIL_CHARS = 500;

/** The body as JSON, or the response refusing it: too large (413) or not JSON (400). */
export async function readJsonBody(
  request: Request
): Promise<{ ok: true; body: unknown } | { ok: false; response: NextResponse }> {
  const read = await readBodyCapped(request, TRIGGER_BODY_MAX_BYTES);
  if (!read.ok) {
    return read.reason === "too-large"
      ? {
          ok: false,
          response: NextResponse.json({ detail: "The request is too large." }, { status: 413 }),
        }
      : {
          ok: false,
          response: NextResponse.json(
            { detail: "The request body must be JSON." },
            { status: 400 }
          ),
        };
  }
  try {
    return { ok: true, body: JSON.parse(read.text) };
  } catch {
    return {
      ok: false,
      response: NextResponse.json({ detail: "The request body must be JSON." }, { status: 400 }),
    };
  }
}

function callerSafeDetail(
  status: number,
  parsed: unknown,
  passthrough: ReadonlySet<number>
): string | null {
  if (!passthrough.has(status)) return null;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  if (typeof detail !== "string" || !detail.trim()) return null;
  return detail.length > MAX_DETAIL_CHARS ? `${detail.slice(0, MAX_DETAIL_CHARS)}…` : detail;
}

/**
 * POST `payload` to the service's `path` and answer the caller with its reply: the body
 * as is with `okStatus` when the guard accepts it, otherwise the service's status with a
 * caller-safe detail. The call stops when the caller goes away or `timeoutMs` passes.
 */
export async function forwardToWorkflows(options: {
  request: Request;
  path: string;
  token: string;
  payload: unknown;
  timeoutMs: number;
  passthrough: ReadonlySet<number>;
  isExpected: (value: unknown) => boolean;
  okStatus: number;
}): Promise<NextResponse> {
  const { request, path, token, payload, timeoutMs, passthrough, isExpected, okStatus } =
    options;
  let upstream: Response;
  let text: string;
  try {
    upstream = await fetch(`${workflowsUrl()}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      // The service never redirects; following one would send the token elsewhere.
      redirect: "manual",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(timeoutMs)]),
    });
    text = await upstream.text();
  } catch {
    // Unreachable, timed out or cut off mid-reply; don't leak the internal host.
    return NextResponse.json({ detail: UNAVAILABLE }, { status: 502 });
  }
  if (upstream.status >= 300 && upstream.status < 400) {
    return NextResponse.json({ detail: UNAVAILABLE }, { status: 502 });
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return NextResponse.json(
      { detail: upstream.ok ? UNEXPECTED : null },
      { status: upstream.ok ? 502 : upstream.status }
    );
  }
  if (!upstream.ok) {
    return NextResponse.json(
      { detail: callerSafeDetail(upstream.status, parsed, passthrough) },
      { status: upstream.status }
    );
  }
  if (!isExpected(parsed)) {
    return NextResponse.json({ detail: UNEXPECTED }, { status: 502 });
  }
  return NextResponse.json(parsed, { status: okStatus });
}
