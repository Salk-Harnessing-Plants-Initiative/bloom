/**
 * Start a trait export job (design D1, D7).
 *
 * Checks run in order and a refusal before the visibility check makes no database
 * call: same origin (403), verified identity (401/503), at least 1,800 s left on the
 * token (401), parameters (422), job limits (429, taking the slot), then the visible,
 * non-empty selection (404/409/502, releasing the slot). The build itself runs in the
 * background; the dialog polls the job's status and downloads the zip when ready.
 */

import packageJson from '@/package.json'
import { buildExport, resolveSelection } from '@/lib/cyl-trait-export/build-export'
import { createExportDb } from '@/lib/cyl-trait-export/db'
import { ExportError } from '@/lib/cyl-trait-export/errors'
import { reserveJob } from '@/lib/cyl-trait-export/jobs'
import { MIN_SESSION_SECONDS } from '@/lib/cyl-trait-export/limits'
import { detail, parseJobRequest, sameOrigin, verifyIdentity } from '@/lib/cyl-trait-export/request'
import { generatorVersion } from '@/lib/cyl-trait-export/sidecar'
import { buildZip } from '@/lib/cyl-trait-export/zip'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const TOO_MANY: Record<'global_limit' | 'memory', string> = {
  global_limit: 'the server is busy with other exports; try again in a few minutes',
  memory: 'the server is busy with other exports; try again in a few minutes',
}

export async function POST(request: Request): Promise<Response> {
  if (!sameOrigin(request)) return detail(403, 'cross-site request refused')
  const identity = await verifyIdentity()
  if (identity instanceof Response) return identity
  // JWT exp is in whole seconds, so compare in whole seconds.
  if (identity.tokenExp - Math.floor(Date.now() / 1000) < MIN_SESSION_SECONDS) {
    return detail(401, 'session expires too soon')
  }
  const parsed = parseJobRequest(new URL(request.url))
  if (typeof parsed === 'string') return detail(422, parsed)

  const reservation = reserveJob(identity.userId)
  if (!reservation.ok) {
    if (reservation.reason === 'user_limit') {
      return detail(429, 'you already have an export running', { job_id: reservation.runningJobId })
    }
    return detail(429, TOO_MANY[reservation.reason])
  }

  const db = createExportDb(identity.token)
  let resolved
  try {
    resolved = await resolveSelection(db, parsed.selection, request.signal)
  } catch (e) {
    reservation.release()
    if (e instanceof ExportError) {
      if (e.kind === 'not_found' || e.kind === 'empty_selection') return detail(404, e.detail)
      if (e.kind === 'selection_changed') return detail(409, e.detail)
      if (e.kind === 'cancelled') return detail(499, e.detail)
      return detail(502, e.detail)
    }
    console.error('[trait-export] selection failed unexpectedly', e)
    return detail(502, 'the selection could not be read')
  }

  const { selection, recipe, chosen } = parsed
  const version = generatorVersion(packageJson.version, process.env.BLOOM_WEB_BUILD_SHA)
  const jobId = reservation.start(identity.tokenExp, async ({ signal, onProgress }) => {
    const built = await buildExport(db, resolved, selection, {
      recipeKey: recipe,
      chosen,
      generatedAt: new Date(),
      version,
      signal,
      onProgress,
    })
    const encoder = new TextEncoder()
    const chunks = await buildZip(
      [
        { name: `${built.stem}.csv`, data: built.csv() },
        { name: `${built.stem}.export.json`, data: [encoder.encode(built.sidecarJson)] },
        { name: `${built.stem}.excluded.csv`, data: [encoder.encode(built.excludedCsv)] },
      ],
      signal
    )
    return { stem: built.stem, chunks }
  })
  if (jobId === null) return detail(409, 'the export was cancelled before it started')
  return Response.json({ job_id: jobId }, { status: 202 })
}

export async function HEAD(): Promise<Response> {
  return detail(405, 'method not allowed')
}
