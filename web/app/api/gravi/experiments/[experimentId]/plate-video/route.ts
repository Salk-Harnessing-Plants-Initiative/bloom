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
import { parseId } from "@/lib/route-params";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Under undici's 300s header timeout, so a slow encode surfaces as our own 504
// rather than an opaque UND_ERR — the encode itself carries on upstream.
const UPSTREAM_TIMEOUT_MS = 240_000;
const PROGRESS_TIMEOUT_MS = 3_000;
const workflowsUrl = () =>
  process.env.WORKFLOWS_URL ?? "http://workflows:5100";

// Which upstream details reach the caller. The test is whether nothing carrying
// that status can name internal infrastructure — a host, an account, an
// environment variable. An object key is not that: any signed-in user can
// already read one.
//
// 503 fails the test: `auth.py` answers it with the auth client's error, which
// names the internal gateway. 502 carries the storage client's. Anything
// unlisted gets our own wording.
const DETAIL_PASSTHROUGH_STATUSES = new Set([404, 413, 422, 429]);

// Answered in our own words rather than the service's or the generic sentence.
// The service says "Invalid or expired token", and "try again shortly, and tell
// the Bloom team" is wrong twice: waiting cannot fix a login, and it is nobody's
// fault. Signing in again is the only thing that helps.
const SESSION_EXPIRED = "Your session has expired. Sign in again.";

// What a caller is told when the upstream detail is suppressed. One sentence for
// every such case: which of them it was is the log's business, and a scientist
// can act on this whichever it is.
const GENERIC_FAILURE =
  "This video could not be made right now. Try again shortly — if it keeps " +
  "happening, let the Bloom team know.";

function callerSafeDetail(status: number, parsed: unknown): string {
  if (status === 401) return SESSION_EXPIRED;
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return GENERIC_FAILURE;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : GENERIC_FAILURE;
}

// Built per call, never shared. A Response carries its body as a stream that is
// spent when it is sent, so one held in a module constant answers the first
// caller and every later one gets an empty body.
const badPlate = () =>
  NextResponse.json(
    {
      detail:
        "plateId must be 1-64 characters of letters, digits, dot, dash or " +
        "underscore, and must begin with a letter or digit",
    },
    { status: 400 }
  );

const badWave = () =>
  NextResponse.json(
    { detail: "waveNumber must be a whole number or null" },
    { status: 400 }
  );

// Marks an answer as never reusable. The poll carries a signed link made for one
// reader and a state a later call is meant to contradict, so a stored copy of
// either is served to the wrong person or long after it stopped being true.
function noStore(response: NextResponse): NextResponse {
  response.headers.set("Cache-Control", "no-store");
  return response;
}

// One vocabulary for a frame count across both answers: a number, or null when
// it is not known. The service says that second case as zero with a flag beside
// it, and zero is a claim about the video rather than a missing record.
function withFrameCount(parsed: unknown): unknown {
  if (typeof parsed !== "object" || parsed === null) return parsed;
  const { frames_unknown: notKnown, ...rest } = parsed as Record<string, unknown>;
  return notKnown ? { ...rest, frames: null } : rest;
}

/** A wave from the request: a whole number, null, or invalid. */
function parseWave(raw: unknown): number | null | undefined {
  if (raw === null || raw === undefined || raw === "" || raw === "null") return null;
  const wave = typeof raw === "string" ? Number(raw) : raw;
  // `Number.isInteger` refuses booleans, NaN and non-numbers on its own, so it
  // is the whole check. Python needs an explicit bool guard here; JS does not.
  if (!Number.isInteger(wave) || (wave as number) < 0) return undefined;
  return wave as number;
}

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

  if (typeof plateId !== "string" || !isValidPlateId(plateId)) return badPlate();
  const wave = parseWave(rawWave);
  if (wave === undefined) return badWave();

  // A short-circuit for the signed-out case, not the authorization decision:
  // the token is forwarded as-is and workflows verifies it against Supabase.
  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json(
      { detail: "Sign in to generate a video." },
      { status: 401 }
    );
  }


  let upstream: Response;
  try {
    upstream = await fetch(
      `${workflowsUrl()}/gravi/experiments/${experiment}/plate-video`,
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
    console.error("plate video: the workflows service could not be reached", err);
    return NextResponse.json(
      { detail: "The video service is unavailable." },
      { status: 502 }
    );
  }

  // Only one plate encodes at a time, so 429 is the ordinary answer to a second
  // click rather than a rare one. Without Retry-After the button has to guess
  // how long to wait, so it is read before anything can return without it.
  const retryAfter = upstream.headers.get("retry-after");
  const waitHint = retryAfter ? { "Retry-After": retryAfter } : undefined;

  const text = await upstream.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    console.error(
      `plate video: the service answered ${upstream.status} with something that is not JSON`
    );
    return NextResponse.json(
      {
        detail: upstream.ok
          ? "Unexpected response from the video service."
          : GENERIC_FAILURE,
      },
      { status: upstream.ok ? 502 : upstream.status, headers: waitHint }
    );
  }

  if (!upstream.ok) {
    return NextResponse.json(
      { detail: callerSafeDetail(upstream.status, parsed) },
      { status: upstream.status, headers: waitHint }
    );
  }

  return NextResponse.json(withFrameCount(parsed), { status: 200 });
}

/** How far a running render has got, or null. Advisory: never fails the poll. */
async function renderProgress(
  experiment: number,
  plateId: string,
  wave: number | null
): Promise<{ stage: string; done: number; total: number } | null> {
  try {
    const session = await getSession();
    if (!session?.access_token) return null;

    const query = new URLSearchParams({ plate_id: plateId });
    if (wave !== null) query.set("wave_number", String(wave));

    const res = await fetch(
      `${workflowsUrl()}/gravi/experiments/${experiment}/plate-video/progress?${query}`,
      {
        headers: { Authorization: `Bearer ${session.access_token}` },
        signal: AbortSignal.timeout(PROGRESS_TIMEOUT_MS),
      }
    );
    if (!res.ok) return null;
    const body = await res.json();
    return typeof body?.stage === "string" &&
      typeof body?.done === "number" &&
      typeof body?.total === "number"
      ? { stage: body.stage, done: body.done, total: body.total }
      : null;
  } catch {
    return null;
  }
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ experimentId: string }> }
) {
  const { experimentId } = await params;
  const experiment = parseId(experimentId);
  if (experiment === null) {
    return noStore(
      NextResponse.json(
        { detail: "experimentId must be a positive integer" },
        { status: 400 }
      )
    );
  }

  const query = new URL(request.url).searchParams;
  const plateId = query.get("plate_id");
  if (typeof plateId !== "string" || !isValidPlateId(plateId))
    return noStore(badPlate());

  const wave = parseWave(query.get("wave_number"));
  if (wave === undefined) return noStore(badWave());

  let stored: Awaited<ReturnType<typeof getStoredPlateVideo>>;
  try {
    stored = await getStoredPlateVideo(experiment, plateId, wave);
  } catch (err) {
    console.error("plate video poll could not reach storage", err);
    stored = { status: "unknown", reason: "the lookup failed" };
  }

  if (stored.status === "unknown") {
    // Not an absence. Saying "no video" here would have the button offer to
    // render a plate that already has one.
    console.warn(
      `plate video poll could not read storage for ${experiment}/${plateId}: ${stored.reason}`
    );
    return noStore(
      NextResponse.json(
        { detail: "Could not check whether this plate has a video." },
        { status: 503 }
      )
    );
  }

  // `frames` is null both when nothing is stored and when a stored video's row
  // is missing. The caller has `download_url` to tell those apart.
  return noStore(
    NextResponse.json({
      ...(stored.status === "present" || query.get("progress") !== "1"
        ? {}
        : { progress: await renderProgress(experiment, plateId, wave) }),
      download_url: stored.status === "present" ? stored.url : null,
      frames: stored.status === "present" ? stored.frames : null,
    })
  );
}
