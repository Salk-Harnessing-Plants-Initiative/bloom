/**
 * The `<stem>.export.json` sidecar, v1 (`_WIKI/SUPABASE/trait-recipes.export.schema.json`;
 * design D5). Every field comes from where trait-recipes.md's field table says; the
 * object is built in schema key order and free-form objects get recursively sorted
 * keys, so the same data always serializes to the same bytes.
 */

import { codePointCompare as byCodePoint } from './csv'
import { ExportError } from './errors'
import type { RecipeRow } from './recipes'
import { buildFilters, scanIdsSha256, selectionScanIds, type Selection } from './selection'

export type CoverageRow = {
  scan_id: number
  experiment_id: number
  plant_qr_code: string
  recipe_key: string | null
  status: 'included' | 'other_recipe' | 'legacy_only' | 'no_traits'
  source_id: number | null
  available_recipes: string[]
}

export type SourceMeta = Record<string, unknown>

type Observed = {
  contract_versions: string[]
  traits_sleap_roots_versions: string[]
  sleap_nn_versions: string[]
  predict_container_digests: string[]
  traits_container_digests: string[]
  inference_configs: Record<string, unknown>[]
}

export type Sidecar = {
  export_schema_version: 1
  generated_at: string
  generated_by: { tool: 'bloom-web'; version: string }
  selection: {
    experiment_ids: number[]
    scan_ids: number[] | null
    filters: Record<string, number>
    scan_ids_sha256: string
  }
  recipe: {
    recipe_key: string
    recipe_key_version: number | null
    recipe_kind: RecipeRow['recipe_kind']
    chosen_by: 'default' | 'user'
    definition: Record<string, unknown> | null
    observed?: Observed
  }
  included: { n_scans: number; source_ids: number[] }
  excluded: {
    scan_id: number
    plant_qr_code: string
    reason: Exclude<CoverageRow['status'], 'included'>
    available_recipes: string[]
  }[]
  other_recipes_in_selection: { recipe_key: string; n_scans: number }[]
}

export type SidecarInput = {
  generatedAt: Date
  version: string
  experimentId: number
  selection: Selection
  selectedIds: number[]
  recipeKey: string
  chosenBy: 'default' | 'user'
  merged: RecipeRow[]
  coverage: CoverageRow[]
  sourceMetadata: Map<number, SourceMeta | null>
}

/** `generated_by.version`: the package version, plus the build sha when deployed with one. */
export function generatorVersion(pkg: string, sha: string | undefined): string {
  return sha ? `${pkg}+${sha}` : pkg
}

/** A JSON value with object keys sorted by code point, recursively. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const k of Object.keys(value).sort(byCodePoint)) {
      out[k] = canonical((value as Record<string, unknown>)[k])
    }
    return out
  }
  return value
}

const distinctSorted = (values: unknown[]): string[] =>
  [...new Set(values.filter((v): v is string => typeof v === 'string'))].sort(byCodePoint)

function observed(sourceIds: number[], meta: Map<number, SourceMeta | null>): Observed {
  const metas = sourceIds.map((id) => meta.get(id)).filter((m): m is SourceMeta => !!m)
  const models = metas.flatMap((m) =>
    Array.isArray(m.predict_models) ? (m.predict_models as Record<string, unknown>[]) : []
  )
  const configs = new Map<string, Record<string, unknown>>()
  for (const m of metas) {
    const c = m.predict_inference_config
    if (c !== null && typeof c === 'object' && !Array.isArray(c)) {
      const sorted = canonical(c) as Record<string, unknown>
      configs.set(JSON.stringify(sorted), sorted)
    }
  }
  return {
    contract_versions: distinctSorted(metas.map((m) => m.contract_version)),
    traits_sleap_roots_versions: distinctSorted(metas.map((m) => m.traits_sleap_roots_version)),
    sleap_nn_versions: distinctSorted(models.map((m) => m.sleap_nn_version)),
    predict_container_digests: distinctSorted(metas.map((m) => m.predict_container_digest)),
    traits_container_digests: distinctSorted(metas.map((m) => m.traits_container_digest)),
    inference_configs: [...configs.keys()].sort(byCodePoint).map((k) => configs.get(k)!),
  }
}

export function buildSidecar(input: SidecarInput): Sidecar {
  const { coverage, selectedIds, recipeKey } = input
  const selected = new Set(selectedIds)
  const covered = new Set(coverage.map((c) => c.scan_id))
  if (
    coverage.length !== covered.size ||
    covered.size !== selected.size ||
    [...selected].some((id) => !covered.has(id))
  ) {
    throw new ExportError(
      'integrity',
      'the included and excluded scans are not exactly the selection'
    )
  }
  const row = input.merged.find((r) => r.recipe_key === recipeKey)
  if (!row) {
    throw new ExportError('not_in_selection', 'this recipe is not in the selection')
  }
  const byScan = [...coverage].sort((a, b) => a.scan_id - b.scan_id)
  const included = byScan.filter((c) => c.status === 'included')
  const sourceIds = [
    ...new Set(included.map((c) => c.source_id).filter((s): s is number => s !== null)),
  ].sort((a, b) => a - b)

  const recipe: Sidecar['recipe'] = {
    recipe_key: row.recipe_key,
    recipe_key_version: row.recipe_key_version,
    recipe_kind: row.recipe_kind,
    chosen_by: input.chosenBy,
    definition:
      row.definition === null ? null : (canonical(row.definition) as Record<string, unknown>),
  }
  if (row.recipe_kind === 'pipeline') recipe.observed = observed(sourceIds, input.sourceMetadata)

  return {
    export_schema_version: 1,
    generated_at: input.generatedAt.toISOString(),
    generated_by: { tool: 'bloom-web', version: input.version },
    selection: {
      experiment_ids: [input.experimentId],
      scan_ids: selectionScanIds(input.selection, selectedIds),
      filters: buildFilters(input.selection),
      scan_ids_sha256: scanIdsSha256(selectedIds),
    },
    recipe,
    included: { n_scans: included.length, source_ids: sourceIds },
    excluded: byScan
      .filter((c) => c.status !== 'included')
      .map((c) => ({
        scan_id: c.scan_id,
        plant_qr_code: c.plant_qr_code,
        reason: c.status as Exclude<CoverageRow['status'], 'included'>,
        available_recipes: c.available_recipes,
      })),
    other_recipes_in_selection: input.merged
      .filter((r) => r.recipe_key !== recipeKey)
      .map((r) => ({ recipe_key: r.recipe_key, n_scans: r.n_scans })),
  }
}

/** 2-space JSON with a trailing LF, keys in the order built above. */
export function serializeSidecar(sidecar: Sidecar): string {
  return JSON.stringify(sidecar, null, 2) + '\n'
}
