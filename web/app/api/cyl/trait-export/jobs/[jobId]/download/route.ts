/**
 * Download a finished trait export (design D1, D7). The zip is streamed from the
 * chunks the job stored, without copying them into one buffer, and counted against the
 * memory budget until the stream ends or is cancelled. 409 until the job is
 * ready; 404 once it has expired, for anyone but its owner, or after a restart.
 */

import { openDownload } from '@/lib/cyl-trait-export/jobs'
import { detail, guardJobRoute } from '@/lib/cyl-trait-export/request'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type Ctx = { params: Promise<{ jobId: string }> }

export async function GET(request: Request, { params }: Ctx): Promise<Response> {
  const guard = await guardJobRoute(request, params)
  if (guard instanceof Response) return guard
  const found = openDownload(guard.identity.userId, guard.jobId)
  if (!found) return detail(404, 'export not found; it may have expired or the server restarted')
  if (found.kind === 'not_ready') return detail(409, `the export is ${found.status}`)

  // The zip's bytes count against the memory budget until the stream ends or the
  // client goes away (tasks.md 10b.2).
  const { chunks, close } = found
  let i = 0
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(chunks[i++])
      else {
        close()
        controller.close()
      }
    },
    cancel() {
      close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': `attachment; filename="${found.filename}"`,
      'Content-Length': String(found.bytes),
      'Cache-Control': 'no-store',
    },
  })
}

export async function HEAD(): Promise<Response> {
  return detail(405, 'method not allowed')
}
