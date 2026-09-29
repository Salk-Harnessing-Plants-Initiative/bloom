/**
 * The checks and response mapping behind POST /api/cyl/pipeline (design D1).
 * They live here rather than in route.ts because Next's build type-check
 * rejects extra exports from a route module.
 */

import { NextResponse } from "next/server";

// Bounds validation, not memory: Next has already buffered up to 10 MB by the
// time the handler runs (design D1).
export const TRIGGER_BODY_MAX_BYTES = 256 * 1024;

// Upstream details are logged and passed through at most this long.
const DETAIL_MAX_CHARS = 300;

const LOG_PREFIX = "[api/cyl/pipeline]";

export function detail(status: number, text: string, headers?: HeadersInit) {
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
 * the request URL, whose host is the `0.0.0.0` bloom-web listens on.
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

export type BodyText = { ok: true; text: string } | { ok: false; tooLarge: true };

/**
 * The body as UTF-8 text, refusing more than `maxBytes` bytes. A declared
 * `Content-Length` over the cap is refused without reading; otherwise the
 * stream is counted as it is read, since the header may be absent or wrong.
 * Invalid UTF-8 comes back as text JSON.parse will refuse.
 */
export async function readBodyCapped(request: Request, maxBytes: number): Promise<BodyText> {
  const declared = request.headers.get("content-length");
  if (declared !== null && /^\d+$/.test(declared) && Number(declared) > maxBytes) {
    return { ok: false, tooLarge: true };
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
      return { ok: false, tooLarge: true };
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
    return { ok: true, text: "�" };
  }
}

function truncate(text: string): string {
  return text.slice(0, DETAIL_MAX_CHARS);
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
export const TIMED_OUT = `The pipeline service did not answer within two minutes, ${MAY_HAVE_STARTED}`;

const FALLBACK_DETAIL: Record<number, string> = {
  401: "Your session has expired. Sign in again to start a run.",
  404: "The pipeline service could not find what this run targets.",
  422: "The pipeline service refused this request as invalid.",
  429: "Too many requests to the pipeline service. Try again shortly.",
};

/**
 * Upstream's answer, reduced to what a caller may see. Only a well-formed
 * success is returned as is; error bodies are written for operators, so only a
 * 404 or 422 string detail passes, truncated.
 */
export async function mapUpstreamResponse(upstream: Response): Promise<NextResponse> {
  const text = await upstream.text();
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
    `${LOG_PREFIX} upstream answered ${upstream.status}: ${truncate(stringDetail ?? text)}`
  );

  switch (upstream.status) {
    case 401:
      return detail(401, FALLBACK_DETAIL[401]);
    case 404:
    case 422:
      return detail(
        upstream.status,
        stringDetail ? truncate(stringDetail) : FALLBACK_DETAIL[upstream.status]
      );
    case 429: {
      const retryAfter = upstream.headers.get("retry-after");
      return detail(
        429,
        FALLBACK_DETAIL[429],
        retryAfter !== null && /^\d+$/.test(retryAfter) ? { "Retry-After": retryAfter } : undefined
      );
    }
    default:
      return detail(502, UNEXPECTED_RESPONSE);
  }
}

/** A rejected upstream `fetch`: 504 for our own timeout, 502 for the rest. */
export function mapUpstreamFailure(err: unknown): NextResponse {
  const name = (err as { name?: unknown } | null)?.name;
  if (name === "TimeoutError") {
    console.error(`${LOG_PREFIX} upstream timed out`);
    return detail(504, TIMED_OUT);
  }
  const code = (err as { cause?: { code?: unknown } } | null)?.cause?.code;
  console.error(
    `${LOG_PREFIX} upstream unreachable: ${String(name ?? "error")}${typeof code === "string" ? ` (${code})` : ""}`
  );
  return detail(502, UNREACHABLE);
}
