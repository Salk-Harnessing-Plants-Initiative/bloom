/**
 * The recipes a selection has, for the download dialog (design D3, D7). Same checks
 * as a job start except the session floor, the recipe/chosen parameters and the job
 * limits. The listing is batched and merged exactly as the job does it. A user has
 * at most one listing in flight: a newer one aborts the older.
 */

import { listMergedRecipes, resolveSelection } from '@/lib/cyl-trait-export/build-export'
import { createExportDb } from '@/lib/cyl-trait-export/db'
import { ExportError } from '@/lib/cyl-trait-export/errors'
import { detail, parseSelection, sameOrigin, verifyIdentity } from '@/lib/cyl-trait-export/request'
import { getExportState } from '@/lib/cyl-trait-export/state'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

export async function GET(request: Request): Promise<Response> {
  if (!sameOrigin(request)) return detail(403, 'cross-site request refused')
  const identity = await verifyIdentity()
  if (identity instanceof Response) return identity
  const selection = parseSelection(new URL(request.url))
  if (typeof selection === 'string') return detail(422, selection)

  const { listings } = getExportState()
  listings.get(identity.userId)?.abort()
  const ctrl = new AbortController()
  listings.set(identity.userId, ctrl)
  const onAbort = () => ctrl.abort()
  request.signal.addEventListener('abort', onAbort, { once: true })

  try {
    const db = createExportDb(identity.token)
    const resolved = await resolveSelection(db, selection, ctrl.signal)
    const ids = resolved.scans.map((s) => s.scan_id)
    const merged = await listMergedRecipes(db, resolved.experiment.id, ids, { signal: ctrl.signal })
    const rows = merged.map((r) => ({
      recipe_key: r.recipe_key,
      recipe_kind: r.recipe_kind,
      recipe_key_version: r.recipe_key_version,
      definition: r.definition,
      n_scans: r.n_scans,
      newest_source_id: r.newest_source_id,
      is_default: r.is_default,
    }))
    return Response.json(
      { n_selected: ids.length, rows },
      { headers: { 'Cache-Control': 'no-store' } }
    )
  } catch (e) {
    if (e instanceof ExportError) {
      if (e.kind === 'empty_selection') return Response.json({ n_selected: 0, rows: [] })
      if (e.kind === 'not_found') return detail(404, e.detail)
      if (e.kind === 'selection_changed') return detail(409, e.detail)
      if (e.kind === 'cancelled') return detail(499, 'the listing was replaced or cancelled')
      return detail(502, e.detail)
    }
    console.error('[trait-export] recipe listing failed unexpectedly', e)
    return detail(502, 'the recipes could not be listed')
  } finally {
    request.signal.removeEventListener('abort', onAbort)
    if (listings.get(identity.userId) === ctrl) listings.delete(identity.userId)
  }
}

export async function HEAD(): Promise<Response> {
  return detail(405, 'method not allowed')
}
