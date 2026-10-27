/**
 * A trait export job's status (GET) and cancel/drop (DELETE), for its owner only
 * (design D1, D7). The status carries only whitelisted fields.
 */

import { deleteJob, getJob } from '@/lib/cyl-trait-export/jobs'
import { detail, guardJobRoute } from '@/lib/cyl-trait-export/request'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type Ctx = { params: Promise<{ jobId: string }> }

const NOT_FOUND = 'export not found; it may have expired or the server restarted'

export async function GET(request: Request, { params }: Ctx): Promise<Response> {
  const guard = await guardJobRoute(request, params)
  if (guard instanceof Response) return guard
  const view = getJob(guard.identity.userId, guard.jobId)
  if (!view) return detail(404, NOT_FOUND)
  return Response.json(view, { headers: { 'Cache-Control': 'no-store' } })
}

export async function DELETE(request: Request, { params }: Ctx): Promise<Response> {
  const guard = await guardJobRoute(request, params)
  if (guard instanceof Response) return guard
  if (!deleteJob(guard.identity.userId, guard.jobId)) return detail(404, NOT_FOUND)
  return new Response(null, { status: 204 })
}

export async function HEAD(): Promise<Response> {
  return detail(405, 'method not allowed')
}
