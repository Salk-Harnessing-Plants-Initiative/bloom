# Add a recipe key to cyl trait sources, recipe-aware reads, and run stamping (bloom#935, bloom#937)

## Why

Every trait consumer assumes that one `cyl_trait_sources` row is one coherent result set. Pipeline
write-back breaks that assumption: it writes **one source per scan**
(`insert_cyl_result_envelope` requires `image_ids` to resolve to exactly one scan). So an
experiment's "latest" values are a per-scan patchwork that can mix models and code, and no
export can say which computation produced it.

The staging database on 2026-09-29, read-only, shows:
- 80 pipeline sources (ids 6–233), and each resolves to exactly one scan;
- 9 distinct computations among them, plus one verification fixture (id 70).

The computation is recorded only inside `metadata` jsonb. Nothing computes it, stores it or
exposes it.

The same sources also record no run. `metadata->>'pipeline_run_id'` and `argo_*` are null on all
80 (bloom#864). Argo deletes a Workflow one hour after it finishes (`WORKFLOWS_K8S_TTL_SECONDS`).
The RPC is handed `p_argo_workflow_name` and uses it only for the run-scan status update. After
that hour, nothing can tell which Bloom run or Workflow produced a value.

## What Changes

- **Recipe key on every source** (bloom#935).
  - `cyl_trait_sources` gains `recipe_key text`, `recipe_key_version smallint` and
    `scan_id bigint` (FK `cyl_scans`).
  - `recipe_key` v1 is the sha256 of a canonical JSON built from four things:
    - the sorted `(registry_id, version, weights_checksum)` model triples;
    - `predict_code_sha`;
    - `traits_code_sha`;
    - `predict_output_params`, which is omitted when null or empty.

    That is the contracts idempotency-key payload without its per-scan inputs (`scan_key`,
    `images_checksum`, `param_hash`).
  - Legacy named sources (NULL `metadata`) get the pseudo-recipe `legacy:<source_id>`. Trait
    rows with a NULL `source_id` read as the pseudo-recipe `unattributed`.
  - One `IMMUTABLE` helper computes the key. Both the backfill and the write-back RPC call it.
  - Every existing source is backfilled.
- **Run and Workflow stamping** (bloom#937).
  - `cyl_trait_sources` gains `argo_workflow_name text` and `cyl_pipeline_run_id bigint` (FK
    `cyl_pipeline_runs`).
  - The run is resolved from `cyl_pipeline_run_scans` by the Workflow name. It stays NULL for
    hand-submitted runs, and for any Workflow name that maps to more than one run.
  - Nothing is backfilled for these two columns: that history was never recorded.
- **Write-back sets all five columns** on a fresh insert. `insert_cyl_result_envelope(jsonb, text)`
  gets a new body copied from the live a9 body. The no-op re-delivery path, the bloom#875
  fallback and the return value stay unchanged, and a no-op never rewrites the stamps.
- **Recipe-aware reads.**
  - New: `list_trait_recipes(experiment_ids_, scan_ids_)` lists the recipes in a selection,
    with scan counts and the default ("newest"). The default is the recipe whose highest
    `source_id` in the selection is highest.
  - New: `get_trait_recipe_coverage(experiment_ids_, scan_ids_, recipe_key_)` gives each
    selected scan one status: `included`, `other_recipe`, `legacy_only` or `no_traits`.
  - Changed: `get_experiment_traits` gains `recipe_key_` and `scan_ids_` and returns a
    `recipe_key` column. **BREAKING (return type):** `DROP FUNCTION` of the 3-arg form, then
    `CREATE`. Callers that pass named arguments and read columns by name are unaffected. That
    includes bloommcp's `SupabaseReader`. The unpinned default path is unchanged.
- **Datasets.**
  - `cyl_datasets` gains `recipe_key text`.
  - `create_cyl_dataset` gains a recipe mode: pass exactly one of `trait_source_id` or
    `recipe_key`. In recipe mode each scan freezes the rows of its newest source with that recipe.
  - This is a signature change: `DROP` the 5-arg form, then `CREATE` the 6-arg form, whose new
    argument has a default. bloomctl's named 5-key call keeps working.
  - A new `cyl-datasets` spec is added; none exists today.
- **Export sidecar v1** is defined in a new docs page, `_WIKI/SUPABASE/trait-recipes.md`: the
  recipe, what was included, and the excluded scans with reasons. A `cyl-trait-read`
  requirement ties every sidecar field to a column these RPCs return. The machine-readable
  schema file lands with its first producer, #865 (web) or #481 (bloomctl). A migration PR
  may not carry non-`.md` files outside the database paths.
- **OpenSpec housekeeping, in this PR.** `add-bulk-trait-read-rpc` (shipped 2026-07-28) is
  archived first, so this change can MODIFY its `get_experiment_traits` requirement against the
  main spec. Its duplicate "Bulk read grants…" header is renamed so the archive applies cleanly.

## Out of scope

- `cyl_pipeline_run_scans.requested` and inserting run-scan rows for unrequested scans (#937 step
  3). The cause, the srp#71 manifest union, is removed by PR #940 once the cluster templates run
  a bloomctl image that includes it. The source-level stamps above still record the run for an
  unrequested scan.
- A `cyl_trait_recipes` table with `retired_at`, so "newest" could skip a rolled-back build. It
  was decided against on 2026-09-29; the gap is recorded in design.md.
- All consumer adoption:
  - the web export and picker (#865, #482);
  - bloomctl `--recipe` for export and datasets (#481);
  - the bloommcp reader, which is Evelyn's, as is #936. Its experiment 1 timeout is its own fix.
  - `get_scan_traits` gains no recipe argument in this change.
- The legacy source 4 (`test`) data fix on experiment 7206207, which is pending a decision.

## Impact

- **Affected specs:**
  - `cyl-trait-writeback`: ADDED requirements only. "Write-back RPC ingests a ResultEnvelope" is
    left untouched because the stale unarchived `fix-cyl-pipeline-run-scan-status` MODIFIES it.
  - `cyl-trait-read`: MODIFIED "Bulk experiment-scoped trait reads", plus ADDED requirements.
  - `cyl-datasets`: new.
- **Affected code:** migrations, rollbacks, integration and unit tests, targeted edits to the five
  `database.types.ts` copies, and `_WIKI` docs. No application code changes, so the PR satisfies
  `lint_migration_isolation`.
- **Existing tests that change:**
  - `tests/integration/test_cyl_experiment_traits.py` and
    `test_cyl_experiment_summary_counts.py`. Both assert `get_experiment_traits(bigint,bigint,text)`
    and `pronargs = 3`.
  - Tests that re-apply older `insert_cyl_result_envelope` bodies. These keep working because
    every new column is nullable.
- **Coordination:**
  - bloommcp (Evelyn) needs no code change to keep working. The new arguments are the durable
    path for #936 ("one recipe per frame").
  - #865, #481 and #482 consume the RPCs.
