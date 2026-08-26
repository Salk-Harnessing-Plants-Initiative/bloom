/**
 * Server-side proxy for on-demand plate time-lapse generation, and the poll
 * that follows when a render outlives the request.
 *
 * POST renders. GET reports whether a video is stored yet — a POST can time out
 * at 240s while the encode carries on upstream (the handler is synchronous, so
 * a client disconnect does not cancel it), leaving the browser with a 504 and
 * no handle on the work.
 *
 * `plate_id` travels in the body, not the path: it is free text, and the
 * cylinder route's integer-only path defence does not transfer to it. The poll
 * takes it as a query parameter, which never reaches an upstream URL path — it
 * only builds a storage key, and `plateVideoPath` refuses one it cannot make.
 *
 * Not a security boundary: Caddy also publishes the service at `/workflows/*`,
 * so the checks here are about what this app does, not what the service
 * permits. Authorization lives upstream.
 *
 * Unlike the cylinder route, this does not refuse a plate that already has a
 * video. A plate keeps gaining captures, so a stored video is usually not
 * wrong — just short. Whether to re-render is the service's decision, and it
 * has the recorded frame count to make it with.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { getStoredPlateVideo } from "@/lib/supabase/plate-video";
import { isValidPlateId } from "@/lib/supabase/plate-video-path";
import { parseId } from "@/components/scan-video.helpers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Under undici's 300s header timeout, so a slow encode surfaces as our own 504
// rather than an opaque UND_ERR — the encode itself carries on upstream.
const UPSTREAM_TIMEOUT_MS = 240_000;

// Upstream details are written for operators. 404 and 413 say something the
// caller needs — "this plate has no captures with an image", "this plate is at
// least 49.0 GB" — and neither names anything internal. Every other status
// falls back to the client's own wording: 5xx carry the internal gateway URL
// and the service account's env key names, and 502 names an object path.
const DETAIL_PASSTHROUGH_STATUSES = new Set([404, 413]);

function callerSafeDetail(status: number, parsed: unknown): string | null {
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return null;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

/** A wave from the request: a whole number, null, or invalid. */
function parseWave(raw: unknown): number | null | undefined {
  if (raw === null || raw === undefined || raw === "") return null;
  const wave = typeof raw === "string" ? Number(raw) : raw;
  // `Number.isInteger` refuses booleans, NaN and non-numbers on its own, so it
  // is the whole check. Python needs an explicit bool guard here; JS does not.
  if (!Number.isInteger(wave) || (wave as number) < 0) return undefined;
  return wave as number;
}

const BAD_PLATE = NextResponse.json(
  {
    detail:
      "plateId must be 1-64 characters of letters, digits, dot, dash or " +
      "underscore, and may not begin with a dot",
  },
  { status: 400 }
);

const BAD_WAVE = NextResponse.json(
  { detail: "waveNumber must be a whole number or null" },
  { status: 400 }
);

export async function POST(
  request: Request,
  { params }: { params: Promise<{ experimentId: string }> }
) {
  const { experimentId } = await params;
  const experiment = parseId(experimentId);
  if (experiment === null) {
    return NextResponse.json(
      { detail: "experimentId must be a positive integer" },
      { status: 400 }
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "expected a JSON body" }, { status: 400 });
  }

  const { plate_id: plateId, wave_number: rawWave } =
    (body as { plate_id?: unknown; wave_number?: unknown }) ?? {};

  if (typeof plateId !== "string" || !isValidPlateId(plateId)) return BAD_PLATE;
  const wave = parseWave(rawWave);
  if (wave === undefined) return BAD_WAVE;

  // A short-circuit for the signed-out case, not the authorization decision:
  // the token is forwarded as-is and workflows verifies it against Supabase.
  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json(
      { detail: "Sign in to generate a video." },
      { status: 401 }
    );
  }

  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

  let upstream: Response;
  try {
    upstream = await fetch(
      `${workflowsUrl}/gravi/experiments/${experiment}/plate-video`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ plate_id: plateId, wave_number: wave }),
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      }
    );
  } catch (err) {
    // A timeout is not a failure — the encode is still running, so say that
    // rather than sending the user back to click Generate again.
    if (err instanceof Error && err.name === "TimeoutError") {
      return NextResponse.json(
        {
          detail:
            "Still encoding — this plate is taking longer than expected. " +
            "Check back shortly.",
        },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { detail: "The video service is unavailable." },
      { status: 502 }
    );
  }

  const text = await upstream.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return NextResponse.json(
      { detail: upstream.ok ? "Unexpected response from the video service." : null },
      { status: upstream.ok ? 502 : upstream.status }
    );
  }

  if (!upstream.ok) {
    return NextResponse.json(
      { detail: callerSafeDetail(upstream.status, parsed) },
      { status: upstream.status }
    );
  }

  return NextResponse.json(parsed, { status: 200 });
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ experimentId: string }> }
) {
  const { experimentId } = await params;
  const experiment = parseId(experimentId);
  if (experiment === null) {
    return NextResponse.json(
      { detail: "experimentId must be a positive integer" },
      { status: 400 }
    );
  }

  const query = new URL(request.url).searchParams;
  const plateId = query.get("plate_id");
  if (typeof plateId !== "string" || !isValidPlateId(plateId)) return BAD_PLATE;

  const wave = parseWave(query.get("wave_number"));
  if (wave === undefined) return BAD_WAVE;

  const stored = await getStoredPlateVideo(experiment, plateId, wave);
  if (stored.status === "unknown") {
    // Not an absence. Saying "no video" here would have the button offer to
    // render a plate that already has one.
    return NextResponse.json(
      { detail: "Could not check whether this plate has a video." },
      { status: 503 }
    );
  }

  return NextResponse.json({
    download_url: stored.status === "present" ? stored.url : null,
  });
}
