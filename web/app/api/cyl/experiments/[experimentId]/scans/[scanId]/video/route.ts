/**
 * Server-side proxy for on-demand cyl scan video generation.
 *
 * The workflows API requires a Supabase user JWT and is reachable in-cluster at
 * `workflows:5100` (both services sit on `supanet` in dev and prod). Proxying
 * here rather than calling it from the browser keeps the service off the public
 * origin, keeps the access token out of client JS, and avoids adding a
 * `NEXT_PUBLIC_*` key just to name a backend.
 *
 * Generation is synchronous upstream — encoding a full rotation takes a while,
 * so callers should expect this request to stay open.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { parseId } from "@/components/scan-video.helpers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const WORKFLOWS_URL = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ experimentId: string; scanId: string }> }
) {
  const { experimentId, scanId } = await params;

  const experiment = parseId(experimentId);
  const scan = parseId(scanId);
  if (experiment === null || scan === null) {
    return NextResponse.json(
      { detail: "experimentId and scanId must be positive integers" },
      { status: 400 }
    );
  }

  // A short-circuit for the signed-out case, not the authorization decision:
  // the token is forwarded as-is and workflows verifies it against Supabase
  // (`GET /auth/v1/user`), so an expired or forged cookie is refused there.
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
      `${WORKFLOWS_URL}/cyl/experiments/${experiment}/scans/${scan}/video`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
      }
    );
  } catch {
    // Unreachable service — don't leak the internal host into the response.
    return NextResponse.json(
      { detail: "The video service is unavailable." },
      { status: 502 }
    );
  }

  // Pass the upstream status through so the client can tell a rate limit from a
  // missing scan. A non-JSON body (a proxy error page) becomes a generic detail
  // rather than a parse crash.
  const body = await upstream.text();
  try {
    return NextResponse.json(JSON.parse(body), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: upstream.ok ? "Unexpected response from the video service." : null },
      { status: upstream.ok ? 502 : upstream.status }
    );
  }
}
