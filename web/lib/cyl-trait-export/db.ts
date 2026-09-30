/**
 * The reads a trait export makes, as a small port (design D2). `createExportDb` backs
 * it with supabase-js as the verified user; tests back it with golden/input.json.
 */

import { createClient } from '@supabase/supabase-js'

import type { TraitRow } from './csv'
import type { ScanExtendedRow } from './metadata'
import type { RecipeRow } from './recipes'
import type { CoverageRow, SourceMeta } from './sidecar'
import { getExportState } from './state'

/** Which scans: an experiment (optionally one wave and/or age), or one scan. */
export type SelectionQuery =
  | { experimentId: number; wave?: number; age?: number }
  | { scanId: number }

/** A PostgREST error, reduced to what the export may act on. Never shown raw. */
export type DbError = { code: string; message: string }

export type ExportDb = {
  /** The experiment if the user can see it through RLS (deleted ones are hidden). */
  experiment(id: number, signal?: AbortSignal): Promise<{ id: number; name: string } | null>
  /** A scan's experiment id, or null if there is no such scan. */
  scanExperiment(scanId: number, signal?: AbortSignal): Promise<number | null>
  /** `cyl_scans_extended` rows with `scan_id > after`, ascending, at most `limit`. */
  pageScans(
    q: SelectionQuery,
    after: number | null,
    limit: number,
    signal?: AbortSignal
  ): Promise<ScanExtendedRow[]>
  /** An exact count of the same selection. */
  countScans(q: SelectionQuery, signal?: AbortSignal): Promise<number>
  accessions(ids: number[], signal?: AbortSignal): Promise<{ id: number; name: string }[]>
  listRecipes(experimentId: number, scanIds: number[], signal?: AbortSignal): Promise<RecipeRow[]>
  coverage(
    experimentId: number,
    scanIds: number[],
    recipeKey: string,
    signal?: AbortSignal
  ): Promise<CoverageRow[]>
  /** Trait rows (4 columns) and PostgREST's exact count for the same call. */
  traits(
    experimentId: number,
    recipeKey: string,
    scanIds: number[],
    signal?: AbortSignal
  ): Promise<{ rows: TraitRow[]; count: number }>
  sourceMetadata(
    ids: number[],
    signal?: AbortSignal
  ): Promise<{ id: number; metadata: SourceMeta | null }[]>
}

export function isDbError(e: unknown): e is DbError {
  return (
    typeof e === 'object' &&
    e !== null &&
    typeof (e as DbError).code === 'string' &&
    typeof (e as DbError).message === 'string' &&
    !(e instanceof Error)
  )
}

type Result = {
  data: unknown
  error: { code: string; message: string } | null
  count?: number | null
}
// A PostgREST builder: chainable, awaitable, and abortable.
type Builder = PromiseLike<Result> & { abortSignal(signal: AbortSignal): Builder }

function nonEmpty(ids: number[], what: string): void {
  // A NULL or missing scan_ids_ makes the recipe RPCs read the whole experiment.
  if (ids.length === 0) throw new Error(`${what} called with no scan ids`)
}

function applySelection<B extends { eq(col: string, v: unknown): B }>(b: B, q: SelectionQuery): B {
  if ('scanId' in q) return b.eq('scan_id', q.scanId)
  let out = b.eq('experiment_id', q.experimentId)
  if (q.wave !== undefined) out = out.eq('wave_number', q.wave)
  if (q.age !== undefined) out = out.eq('plant_age_days', q.age)
  return out
}

/**
 * ExportDb over supabase-js, as the verified user: the client sends the captured
 * access token on every request and has no auth session to refresh (design D2
 * "Robustness"). Every request passes through the process-wide semaphore and carries
 * an abort signal.
 */
export function createExportDb(accessToken: string): ExportDb {
  const client = createClient(
    process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { accessToken: async () => accessToken }
  )
  // The client is untyped here: the recipe RPCs' generated types are looser than
  // the rows the functions return, and every result is shaped explicitly below.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sb = client as any

  async function send(make: () => Builder, signal?: AbortSignal): Promise<Result> {
    const { semaphore } = getExportState()
    const res = await semaphore.run((sig) => make().abortSignal(sig), signal)
    if (res.error) throw { code: res.error.code, message: res.error.message } satisfies DbError
    return res
  }

  return {
    async experiment(id, signal) {
      const r = await send(
        () => sb.from('cyl_experiments').select('id,name').eq('id', id).maybeSingle(),
        signal
      )
      return (r.data as { id: number; name: string } | null) ?? null
    },
    async scanExperiment(scanId, signal) {
      const r = await send(
        () =>
          sb.from('cyl_scans_extended').select('experiment_id').eq('scan_id', scanId).maybeSingle(),
        signal
      )
      return (r.data as { experiment_id: number } | null)?.experiment_id ?? null
    },
    async pageScans(q, after, limit, signal) {
      const r = await send(() => {
        let b = applySelection(sb.from('cyl_scans_extended').select('*'), q)
        if (after !== null) b = b.gt('scan_id', after)
        return b.order('scan_id', { ascending: true }).limit(limit)
      }, signal)
      return (r.data as ScanExtendedRow[]) ?? []
    },
    async countScans(q, signal) {
      const r = await send(
        () =>
          applySelection(
            sb.from('cyl_scans_extended').select('scan_id', { count: 'exact', head: true }),
            q
          ),
        signal
      )
      return r.count ?? 0
    },
    async accessions(ids, signal) {
      const r = await send(() => sb.from('accessions').select('id,name').in('id', ids), signal)
      return (r.data as { id: number; name: string }[]) ?? []
    },
    async listRecipes(experimentId, scanIds, signal) {
      nonEmpty(scanIds, 'list_trait_recipes')
      const r = await send(
        () => sb.rpc('list_trait_recipes', { experiment_ids_: [experimentId], scan_ids_: scanIds }),
        signal
      )
      return (r.data as RecipeRow[]) ?? []
    },
    async coverage(experimentId, scanIds, recipeKey, signal) {
      nonEmpty(scanIds, 'get_trait_recipe_coverage')
      const r = await send(
        () =>
          sb.rpc('get_trait_recipe_coverage', {
            experiment_ids_: [experimentId],
            scan_ids_: scanIds,
            recipe_key_: recipeKey,
          }),
        signal
      )
      return (r.data as CoverageRow[]) ?? []
    },
    async traits(experimentId, recipeKey, scanIds, signal) {
      nonEmpty(scanIds, 'get_experiment_traits')
      const r = await send(
        () =>
          sb
            .rpc(
              'get_experiment_traits',
              { experiment_id_: experimentId, recipe_key_: recipeKey, scan_ids_: scanIds },
              { count: 'exact' }
            )
            .select('scan_id,trait_name,source_id,trait_value'),
        signal
      )
      const rows = (r.data as TraitRow[]) ?? []
      return { rows, count: r.count ?? -1 }
    },
    async sourceMetadata(ids, signal) {
      const r = await send(
        () => sb.from('cyl_trait_sources').select('id,metadata').in('id', ids),
        signal
      )
      return (r.data as { id: number; metadata: SourceMeta | null }[]) ?? []
    },
  }
}
