/**
 * The checks, upstream call and response mapping behind POST /api/cyl/pipeline
 * (design D1). They live here, not in route.ts, per tasks.md's rule that
 * helpers never live in route modules; route.ts only orders the steps.
 */

import { NextResponse } from "next/server";

// Bounds validation, not memory: Next has already buffered up to 10 MB of the
// body by the time the handler runs (design D1).
export const TRIGGER_BODY_MAX_BYTES = 256 * 1024;

// Under undici's 300 s header timeout, so a slow trigger surfaces as our own
// 504 rather than an opaque undici error. The largest `scan_ids` trigger (5000
// scans) makes 200 sequential enqueue calls before it answers; wave and
// experiment targets have no scan cap, and their size is PR 5's concern (#901).
export const UPSTREAM_TIMEOUT_MS = 120_000;

// Upstream details are logged and passed through at most this long.
const DETAIL_MAX_CHARS = 300;

const LOG_PREFIX = "[api/cyl/pipeline]";

export function detailResponse(status: number, text: string, headers?: HeadersInit) {
  return NextResponse.json({ detail: text }, { status, headers });
}

/** Media type from `Content-Type`, split on `;`, trimmed and lowercased. */
export function isJsonMediaType(headers: Headers): boolean {
  const mediaType = (headers.get("content-type") ?? "").split(";")[0];
  return mediaType.trim().toLowerCase() === "application/json";
}

/**
 * Whether `Origin` names this host, including its port. The host is the first
 * `x-forwarded-host` value when that header is present, otherwise `Host`; never
 * the request URL, whose host is the `0.0.0.0` bloom-web listens on. (Next
 * itself fills in `x-forwarded-host` from `Host` when it is absent, so behind
 * `next start` the first branch is the one that runs.)
 */
export function isSameOrigin(headers: Headers): boolean {
  const origin = headers.get("origin");
  if (!origin || origin === "null") return false;
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  const forwardedHost = headers.get("x-forwarded-host");
  const host = forwardedHost !== null ? forwardedHost.split(",")[0] : headers.get("host");
  const expected = host?.trim().toLowerCase();
  return !!expected && originHost.toLowerCase() === expected;
}

export type BodyText =
  | { ok: true; text: string }
  | { ok: false; reason: "too-large" | "not-utf8" };

/**
 * The body as UTF-8 text, refusing more than `maxBytes` bytes. A declared
 * `Content-Length` over the cap is refused without the handler reading the
 * stream; otherwise the stream is counted as it is read, since the header may
 * be absent or wrong.
 */
export async function readBodyCapped(request: Request, maxBytes: number): Promise<BodyText> {
  const declared = request.headers.get("content-length");
  if (declared !== null && /^\d+$/.test(declared) && Number(declared) > maxBytes) {
    return { ok: false, reason: "too-large" };
  }
  if (!request.body) return { ok: true, text: "" };

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return { ok: false, reason: "too-large" };
    }
    chunks.push(value);
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return { ok: true, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, reason: "not-utf8" };
  }
}

/** At most DETAIL_MAX_CHARS characters, ending in "…" when anything was cut. */
function truncate(text: string): string {
  return text.length <= DETAIL_MAX_CHARS ? text : `${text.slice(0, DETAIL_MAX_CHARS - 1)}…`;
}

function isSafeInteger(value: unknown): boolean {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isTriggerResult(parsed: unknown): boolean {
  if (typeof parsed !== "object" || parsed === null) return false;
  const { pipeline_run_id, scan_count } = parsed as Record<string, unknown>;
  return isSafeInteger(pipeline_run_id) && isSafeInteger(scan_count);
}

// Neither a 502 nor a 504 proves nothing was written: the trigger has no
// idempotency key, and it inserts the run before it enqueues (design D1).
const MAY_HAVE_STARTED =
  "so the run may or may not have started. Check the runs list before trying again.";

export const UNEXPECTED_RESPONSE = `The pipeline service returned an unexpected response, ${MAY_HAVE_STARTED}`;
export const UNREACHABLE = `Could not reach the pipeline service, ${MAY_HAVE_STARTED}`;
export const TIMED_OUT = `The pipeline service did not answer within ${UPSTREAM_TIMEOUT_MS / 1000} seconds, ${MAY_HAVE_STARTED}`;

export const FALLBACK_DETAIL = {
  401: "Your session has expired. Sign in again to start a run.",
  404: "The pipeline service could not find what this run targets.",
  422: "The pipeline service refused this request as invalid.",
  429: "Too many requests to the pipeline service. Try again shortly.",
} as const;

/**
 * Upstream's answer, reduced to what a caller may see. Only a well-formed
 * success is returned as is; error bodies are written for operators, so only a
 * 404 or 422 string detail passes, truncated.
 */
function mapUpstreamResponse(upstream: Response, text: string): NextResponse {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }

  if (upstream.ok && isTriggerResult(parsed)) {
    return NextResponse.json(parsed, { status: upstream.status });
  }

  const upstreamDetail = (parsed as { detail?: unknown } | null | undefined)?.detail;
  const stringDetail = typeof upstreamDetail === "string" ? upstreamDetail : null;
  console.error(
    `${LOG_PREFIX} upstream answered ${upstream.status}: ${JSON.stringify(truncate(stringDetail ?? text))}`
  );

  switch (upstream.status) {
    case 401:
      return detailResponse(401, FALLBACK_DETAIL[401]);
    case 404:
    case 422:
      return detailResponse(
        upstream.status,
        stringDetail ? truncate(stringDetail) : FALLBACK_DETAIL[upstream.status]
      );
    case 429: {
      const retryAfter = upstream.headers.get("retry-after");
      return detailResponse(
        429,
        FALLBACK_DETAIL[429],
        retryAfter !== null && /^\d+$/.test(retryAfter) ? { "Retry-After": retryAfter } : undefined
      );
    }
    default:
      return detailResponse(502, UNEXPECTED_RESPONSE);
  }
}

/**
 * A failed upstream exchange: 504 for our own timeout, 502 for the rest.
 * `answeredStatus` is set when the failure came while reading an answer whose
 * headers had arrived, so the log keeps what upstream had said.
 */
function mapUpstreamFailure(err: unknown, answeredStatus?: number): NextResponse {
  const name = (err as { name?: unknown } | null)?.name;
  const when = answeredStatus === undefined ? "" : ` after it answered ${answeredStatus}`;
  if (name === "TimeoutError") {
    console.error(`${LOG_PREFIX} upstream timed out${when}`);
    return detailResponse(504, TIMED_OUT);
  }
  const code = (err as { cause?: { code?: unknown } } | null)?.cause?.code;
  console.error(
    `${LOG_PREFIX} upstream failed${when}: ${String(name ?? "error")}${typeof code === "string" ? ` (${code})` : ""}`
  );
  return detailResponse(502, UNREACHABLE);
}

/**
 * POST `body` to the trigger once, never retrying: a retry could start a second
 * run. The timeout covers reading the answer too, so a failure after the
 * headers arrive maps like one before them.
 */
export async function forwardToTrigger(
  workflowsUrl: string,
  accessToken: string,
  body: unknown
): Promise<NextResponse> {
  let upstream: Response;
  let answeredStatus: number | undefined;
  let text: string;
  try {
    upstream = await fetch(`${workflowsUrl}/pipeline`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      // A redirect is not an answer this route knows; it maps to 502.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    answeredStatus = upstream.status;
    text = await upstream.text();
  } catch (err) {
    return mapUpstreamFailure(err, answeredStatus);
  }
  return mapUpstreamResponse(upstream, text);
}
