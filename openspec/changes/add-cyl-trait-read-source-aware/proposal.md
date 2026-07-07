## Why

The write-back RPC (`insert_cyl_result_envelope`, merged #371) mints a **new** `cyl_trait_sources` row
per pipeline run, so a reprocessed scan now carries **multiple** sources and the source-blind
`get_scan_traits(experiment_id_, trait_name_)` returns **duplicate/ambiguous rows** (one per source).
This change makes the cyl trait READ path source-aware — latest source per scan by default, with
optional source-pin and pipeline-run grouping — and is backward-compatible for existing callers
(roadmap A2 "read-path", bloom#298; read-path ≡ bloom-mcp data-access "Supabase data access" section,
"shared source-aware reads, latest = max(id)"). No source-aware read view/RPC exists yet, so this
builds the **single shared** read surface rather than a divergent second one.

## What Changes

- **New shared substrate view `cyl_scan_traits_source`** — the single source of truth for source-aware
  reads. One row per `cyl_scan_traits` row, exposing `scan_id, trait_id, trait_name, value, source_id,
source_name, pipeline_run_id (= cyl_trait_sources.metadata->>'pipeline_run_id'), is_latest`, where
  `is_latest = source_id IS NOT DISTINCT FROM max(source_id) OVER (PARTITION BY scan_id)` (single-pass;
  legacy NULL-source rows count as latest). Every other read object is defined on top of this view, so
  the "latest = max(id)" rule lives in exactly one place. Carrying `trait_name`/`source_id`/`is_latest`
  also makes the view directly usable for a future scan-grain repoint of source-blind readers.
- **New `cyl_scan_traits_latest` view** = `cyl_scan_traits_source WHERE is_latest` — the canonical
  latest-per-scan surface for direct PostgREST readers.
- **MODIFY `get_scan_traits`** to a single function
  `get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT, source_id_ BIGINT DEFAULT NULL,
run_id_ TEXT DEFAULT NULL)`: both NULL ⇒ latest per scan; `source_id_` ⇒ pin one source (scan
  grain); `run_id_` ⇒ each scan's values from one pipeline run (experiment grain, "as of run X" — the
  latest delivery per scan within that run, so a run that delivered a scan twice does not duplicate
  rows); both set ⇒ error. The legacy 2-arg function is dropped and replaced by this one (no PostgREST
  overload ambiguity); 2-arg callers keep working via the defaults.
- **MODIFY `cyl_scan_trait_names`** to list `DISTINCT` trait names present in
  `cyl_scan_traits_latest` (source-disambiguated; restores its "names actually measured" intent).
- **Forward-only migration** + companion manual rollback under `supabase/rollbacks/`; regenerate the
  tracked `database.types.ts` files.

## Impact

- Affected specs: **new** capability `cyl-trait-read`.
- Affected code:
  - `supabase/migrations/` (new read-path migration), `supabase/rollbacks/` (companion rollback).
  - Five tracked `database.types.ts` copies (hand-edited, mirroring #371): `web/lib/database.types.ts`,
    `web/types/database.types.ts`, `packages/bloom-js/src/types/database.types.ts`,
    `packages/bloom-fs/src/types/database.types.ts`,
    `packages/bloom-nextjs-auth/src/lib/database.types.ts`.
  - `_WIKI/BLOOMMCP/README.md` — in the **"Supabase data access"** section, **rewrite** the
    `client.table("cyl_scan_traits")` read example to read `cyl_scan_traits_latest` (and mention
    `cyl_scan_traits_source` for the source/run dimension), so the doc stops teaching the source-blind
    pattern. Cross-reference the spec/migration for the latest-selection rule; do not restate it.
- Backward compatible: the only 2-arg `get_scan_traits` call site
  (`web/app/app/traits/[speciesId]/[experimentId]/TraitExplorer.tsx:194`) is unchanged — the two new
  args are optional, so it binds the defaults; for single-source scans behaviour is identical, and for
  multi-source scans it now returns the latest instead of duplicates. Reads stay open to the existing
  read roles (no RLS or write-grant change).
- **Non-goals (deferred), tracked in one broadened sibling issue** "repoint source-blind cyl trait
  readers at the latest-source surface":
  - Rebuilding the `cyl_trait_by_experiment_wave` aggregate view (langchain `cyl_tools.py` + web traits
    page) to be source/run-aware — it currently **double-counts across sources**.
  - Repointing `langchain/tools/cyl_tools.py` `get_scan_traits_tool` (a source-blind direct read of
    `cyl_scan_traits`) at the new scan-grain surface. Note it is **already broken** independent of this
    change — it selects `trait_name`, a column dropped from `cyl_scan_traits` in the 20241119 refactor —
    and `context_tools.py` `CONTEXT_CYL` documents that stale schema (lists the dropped `trait_name`,
    omits `source_id`). The substrate view exposes `trait_name`/`source_id`/`is_latest` to make this
    repoint a trivial follow-up.
  - Change B (image-grain `source_id`) stays deferred and is not a blocker — this read path is for
    `cyl_scan_traits`, which already has `source_id`.
