import { NextResponse } from 'next/server'

// Liveness probe for the bloom-web container's Docker healthcheck. Returns
// 200 if the Next.js process is serving, independent of upstream health
// (Supabase, Kong, DB). This is intentional: a liveness probe should trigger
// container restart only when the process itself is dead, not when a
// downstream dep is flaking. Readiness-style dep-checks would cause restart
// loops during transient Supabase or DB hiccups. Do NOT add upstream pings
// here without moving to a separate `/api/ready` route.
//
// `force-dynamic` prevents Next from pre-rendering/caching this response at
// build time — stale cached 200s would make the container appear healthy
// even if the process were dead.
export const dynamic = 'force-dynamic'

export async function GET() {
  return NextResponse.json({
    ok: true,
    commit: process.env.NEXT_PUBLIC_COMMIT_SHA ?? 'dev',
  })
}
