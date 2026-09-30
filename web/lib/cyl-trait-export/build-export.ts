/**
 * The batched trait export (design D2, D3).
 *
 * `resolveSelection` runs before the job is accepted: the experiment must be visible
 * through RLS, and the selection is paged by keyset and checked against an exact
 * count. `buildExport` then lists recipes per batch and merges them, and for each
 * batch reads coverage and the chosen recipe's traits, always with the explicit key
 * and a non-empty `scan_ids_`. Any RPC error or integrity failure throws an
 * ExportError with a fixed detail; nothing partial is returned.
 */

import { chunk as chunkIds, pageSelection, type Selection } from './selection'
import { csvRows, csvSlices, TraitPivot, type CsvScan, type TraitRow } from './csv'
import { isDbError, type DbError, type ExportDb, type SelectionQuery } from './db'
import { ExportError, SELECTION_CHANGED } from './errors'
import { excludedCsv } from './excluded'
import { BATCH_SCANS, CSV_SLICE_CELLS, PG_CONCURRENCY, SELECTION_PAGE_SIZE } from './limits'
import { metadataCells, type ScanExtendedRow } from './metadata'
import { mergeRecipeListings, resolveChosenBy } from './recipes'
import { buildSidecar, serializeSidecar, type CoverageRow, type SourceMeta } from './sidecar'
import { pool } from './state'
import { buildStem } from './stem'

export type ResolvedSelection = {
  experiment: { id: number; name: string }
  scans: ScanExtendedRow[]
}

export type Progress = { phase: 'recipes' | 'traits' | 'metadata'; done: number; total: number }

export type BuildOptions = {
  recipeKey: string
  chosen: 'default' | 'user'
  generatedAt: Date
  version: string
  batchSize?: number
  concurrency?: number
  signal?: AbortSignal
  onProgress?: (p: Progress) => void
}

export type BuiltExport = {
  stem: string
  csv: () => Iterable<Uint8Array>
  sidecarJson: string
  excludedCsv: string
  includedScans: number
}

const cancelled = () => new ExportError('cancelled', 'the export was cancelled')

/** A fixed, user-safe detail for a PostgREST error (design D7). */
export function rpcDetail(e: DbError, what: string, batch: number, of: number): string {
  if (e.code === 'PGRST202') return 'export not available yet'
  if (e.code === 'PGRST301' || e.code === 'PGRST303') return 'session expired; sign in again'
  if (e.code === '57014') {
    return `a ${what} read timed out (57014) in batch ${batch} of ${of}; try a wave or age filter`
  }
  return `a ${what} read failed (${e.code}) in batch ${batch} of ${of}`
}

async function guarded<T>(
  signal: AbortSignal | undefined,
  what: string,
  batch: number,
  of: number,
  fn: () => Promise<T>
): Promise<T> {
  if (signal?.aborted) throw cancelled()
  try {
    return await fn()
  } catch (e) {
    if (e instanceof ExportError) throw e
    if (signal?.aborted) throw cancelled()
    if (isDbError(e)) throw new ExportError('rpc', rpcDetail(e, what, batch, of))
    throw e
  }
}

function query(sel: Selection, experimentId: number): SelectionQuery {
  if ('scan' in sel) return { scanId: sel.scan }
  return { experimentId, wave: sel.wave, age: sel.age }
}

export async function resolveSelection(
  db: ExportDb,
  sel: Selection,
  signal?: AbortSignal
): Promise<ResolvedSelection> {
  return guarded(signal, 'selection', 1, 1, async () => {
    let experimentId: number
    if ('scan' in sel) {
      const found = await db.scanExperiment(sel.scan, signal)
      if (found === null) throw new ExportError('not_found', 'no such scan')
      experimentId = found
    } else {
      experimentId = sel.experiment
    }
    const experiment = await db.experiment(experimentId, signal)
    if (experiment === null) throw new ExportError('not_found', 'experiment not found')
    const q = query(sel, experimentId)
    const scans = await pageSelection(
      (after, limit) => db.pageScans(q, after, limit, signal),
      () => db.countScans(q, signal),
      SELECTION_PAGE_SIZE
    )
    if (scans.length === 0) throw new ExportError('not_found', 'no scans match this selection')
    return { experiment, scans }
  })
}

export async function buildExport(
  db: ExportDb,
  resolved: ResolvedSelection,
  sel: Selection,
  opts: BuildOptions
): Promise<BuiltExport> {
  const { signal, recipeKey: key } = opts
  const size = opts.batchSize ?? BATCH_SCANS
  const limit = opts.concurrency ?? PG_CONCURRENCY
  const expId = resolved.experiment.id
  const ids = resolved.scans.map((s) => s.scan_id as number)
  const chunks = chunkIds(ids, size)
  const n = chunks.length
  const numbered = chunks.map((scanIds, i) => ({ scanIds, batch: i + 1 }))

  // Recipes, merged across batches (D2 step 3).
  let listed = 0
  const listings = await pool(numbered, limit, async ({ scanIds, batch }) => {
    const rows = await guarded(signal, 'recipe listing', batch, n, () =>
      db.listRecipes(expId, scanIds, signal)
    )
    opts.onProgress?.({ phase: 'recipes', done: ++listed, total: n })
    return rows
  })
  const merged = mergeRecipeListings(chunks.map((scanIds, i) => ({ scanIds, rows: listings[i] })))
  const recipe = merged.find((r) => r.recipe_key === key)
  if (!recipe) throw new ExportError('not_in_selection', 'this recipe is not in the selection')
  const chosenBy = resolveChosenBy(opts.chosen, key, merged)

  // Coverage and traits per batch (D2 steps 4-6).
  const pivot = new TraitPivot()
  const coverage: CoverageRow[] = []
  let traitsDone = 0
  await pool(numbered, limit, async ({ scanIds, batch }) => {
    const rows = await guarded(signal, 'coverage', batch, n, () =>
      db.coverage(expId, scanIds, key, signal)
    )
    const inBatch = new Set(scanIds)
    const byScan = new Map<number, CoverageRow>()
    for (const r of rows) {
      if (!inBatch.has(r.scan_id) || byScan.has(r.scan_id)) {
        throw new ExportError('integrity', `a coverage row fell outside batch ${batch} of ${n}`)
      }
      byScan.set(r.scan_id, r)
    }
    for (const id of scanIds) {
      if (!byScan.has(id)) throw new ExportError('integrity', `scan ${id} has no coverage row`)
    }
    const included = rows.filter((r) => r.status === 'included')
    if (included.length > 0) {
      const includedIds = included.map((r) => r.scan_id)
      const got = await guarded(signal, 'trait', batch, n, () =>
        db.traits(expId, key, includedIds, signal)
      )
      checkTraits(got.rows, got.count, byScan, new Set(includedIds), batch, n)
      pivot.add(got.rows)
    }
    coverage.push(...rows)
    opts.onProgress?.({ phase: 'traits', done: ++traitsDone, total: n })
  })
  const includedTotal = coverage.filter((c) => c.status === 'included').length
  if (includedTotal !== recipe.n_scans) {
    throw new ExportError('selection_changed', SELECTION_CHANGED)
  }

  // Genotypes and source metadata (D2 steps 1, 7; checks j, k).
  const accessionIds = [
    ...new Set(
      resolved.scans.map((s) => s.accession_id).filter((a): a is number => typeof a === 'number')
    ),
  ]
  const genotypes = new Map<number, string>()
  for (const rows of await pool(chunkIds(accessionIds, size), limit, (part) =>
    guarded(signal, 'accession', 1, 1, () => db.accessions(part, signal))
  )) {
    for (const a of rows) genotypes.set(a.id, a.name)
  }
  for (const a of accessionIds) {
    if (!genotypes.has(a)) throw new ExportError('integrity', `accession ${a} returned no row`)
  }
  const includedRows = coverage.filter((c) => c.status === 'included')
  const sourceIds = [
    ...new Set(includedRows.map((c) => c.source_id).filter((s): s is number => s !== null)),
  ]
  const sourceMeta = new Map<number, SourceMeta | null>()
  for (const rows of await pool(chunkIds(sourceIds, size), limit, (part) =>
    guarded(signal, 'source metadata', 1, 1, () => db.sourceMetadata(part, signal))
  )) {
    for (const s of rows) sourceMeta.set(s.id, s.metadata)
  }
  for (const s of sourceIds) {
    if (!sourceMeta.has(s)) throw new ExportError('integrity', `source ${s} returned no row`)
  }
  opts.onProgress?.({ phase: 'metadata', done: 1, total: 1 })

  const sidecar = buildSidecar({
    generatedAt: opts.generatedAt,
    version: opts.version,
    experimentId: expId,
    selection: sel,
    selectedIds: ids,
    recipeKey: key,
    chosenBy,
    merged,
    coverage,
    sourceMetadata: sourceMeta,
  })
  const ext = new Map(resolved.scans.map((s) => [s.scan_id as number, s]))
  const csvScans: CsvScan[] = includedRows.map((c) => {
    const row = ext.get(c.scan_id)!
    const acc = typeof row.accession_id === 'number' ? genotypes.get(row.accession_id) : undefined
    return { scanId: c.scan_id, meta: metadataCells(row, acc ?? null), sourceId: c.source_id }
  })
  return {
    stem: buildStem({
      experimentName: resolved.experiment.name,
      experimentId: expId,
      selection: sel,
      recipeKey: key,
      generatedAt: opts.generatedAt,
    }),
    csv: () => csvSlices(csvRows(pivot, csvScans, key), CSV_SLICE_CELLS),
    sidecarJson: serializeSidecar(sidecar),
    excludedCsv: excludedCsv(coverage),
    includedScans: includedTotal,
  }
}

/** Checks (c), (d), (f) and the exact count for one batch's trait rows. */
function checkTraits(
  rows: TraitRow[],
  count: number,
  coverage: Map<number, CoverageRow>,
  requested: Set<number>,
  batch: number,
  n: number
): void {
  if (rows.length !== count) {
    throw new ExportError('integrity', `a trait read was truncated in batch ${batch} of ${n}`)
  }
  const seen = new Set<number>()
  for (const r of rows) {
    if (!requested.has(r.scan_id)) {
      throw new ExportError(
        'integrity',
        `a trait row for scan ${r.scan_id}, which was not requested`
      )
    }
    if (r.source_id !== coverage.get(r.scan_id)!.source_id) {
      throw new ExportError('selection_changed', SELECTION_CHANGED)
    }
    seen.add(r.scan_id)
  }
  for (const id of requested) {
    if (!seen.has(id)) throw new ExportError('integrity', `scan ${id} returned no trait rows`)
  }
}
