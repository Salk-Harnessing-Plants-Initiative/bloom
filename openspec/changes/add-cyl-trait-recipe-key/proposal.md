# Add a recipe key to cyl trait sources, recipe-aware reads, and run stamping (bloom#935, bloom#937)

## Why

Pipeline write-back stores **one `cyl_trait_sources` row per scan**, so an experiment's latest
values are a per-scan patchwork. That patchwork can mix models and code, and no export can say
how its values were computed. On staging (2026-09-29), 79 real per-scan pipeline sources hold 9
distinct recipes, and no pipeline source records the Bloom run or Argo Workflow that wrote it
(design § Context).

## What Changes

- **A recipe key on every source (bloom#935).**
  - `cyl_trait_sources` gains `recipe_key`, `recipe_key_version` and `scan_id`.
  - `recipe_key` v1 hashes the sleap-roots-contracts idempotency payload with its per-scan inputs
    removed (design D1).
  - Legacy sources get the pseudo-recipe `legacy:<id>`.
  - Trait rows with no source read as `unattributed`.
  - Every existing source is backfilled.
- **Run and Workflow stamping (bloom#937).**
  - `cyl_trait_sources` gains `argo_workflow_name` and `cyl_pipeline_run_id`.
  - Both are set by `insert_cyl_result_envelope` when a delivery creates a source.
  - They are not backfilled, because the history was never recorded.
- **Recipe-aware reads.** Two new functions:

  - `list_trait_recipes(experiment_ids_, scan_ids_)` returns the recipes in a selection, with scan
    counts and a default recipe;
  - `get_trait_recipe_coverage(experiment_ids_, scan_ids_, recipe_key_)` gives a status for each
    scan.

  `get_experiment_traits` gains `recipe_key_` and `scan_ids_`. The breaking parts:

  - **BREAKING (return type):** `get_experiment_traits` gains a trailing `recipe_key` column, so its
    3-argument form is dropped and replaced. Named callers that read columns by name, including
    bloommcp's reader, are unaffected.
  - **BREAKING (access):** `anon` loses `EXECUTE` on `get_experiment_traits`, which it held on
    staging on 2026-09-29 through Supabase default privileges (design D6).

- **Datasets.**
  - `cyl_datasets` gains `recipe_key`.
  - `create_cyl_dataset` gains a recipe mode (new `cyl-datasets` spec).
  - **BREAKING (behavior):** `create_cyl_dataset` with a NULL `trait_source_id` and no
    `recipe_key` now raises. Before, it created an empty dataset. bloomctl never sends NULL
    (`bloomcli/src/bloomctl/cyl/datasets.py:303-317`).
- **Export sidecar v1.** A reader-facing page, a JSON Schema and an example, under
  `_WIKI/SUPABASE/`.
- **OpenSpec housekeeping.** `add-bulk-trait-read-rpc`, which shipped on 2026-07-28, is archived
  first. That lets this change MODIFY its `get_experiment_traits` requirement.

## Out of scope

- **Unrequested run-scan rows**, #937 step 3 (design D9).
- **Recipe retirement** (design D10).
- **Consumers:** web export and selection (#865, #482); bloomctl `--recipe` (#481); the bloommcp
  reader, owned by egao28 (#936).
- **The experiment 1 bulk-read timeout.** That is #936's own fix.
- **`get_scan_traits` recipe arguments.**
- **The legacy source 4 (`test`) data fix** on experiment 7206207. That is a separate decision for
  eberrigan and Benfica (@blm3886).

## Impact

- **Affected specs:**
  - `cyl-trait-writeback`: ADDED only.
  - `cyl-trait-read`: MODIFIED "Bulk experiment-scoped trait reads" and "Additive,
    non-destructive bulk-read migration", plus ADDED requirements.
  - `cyl-datasets`: new capability.
- **Affected code:** four migrations and their rollbacks in `supabase/`; tests in
  `tests/integration/` and `tests/unit/`; the five `database.types.ts` copies; and docs in
  `_WIKI/`, `contracts/README.md`, `services/workflows/README.md` and `bloomcli/README.md` (`.md`
  only). There are no application code changes, so the PR stays inside `lint_migration_isolation`'s
  surface.
- **Existing tests that change:**

  - `tests/integration/test_cyl_experiment_traits.py`
  - `tests/integration/test_cyl_experiment_summary_counts.py`
  - `tests/integration/test_cyl_pipeline_dispatch.py`
  - `tests/integration/test_cyl_writeback_rpc.py`, which gains tests

  The reasons are in tasks 2.6 and 4.10. `test_cyl_experiment_trait_counts.py`, which deletes a scan after
  write-back, must keep passing unchanged. The new foreign keys use `ON DELETE SET NULL` for that
  reason.

- **Services affected at runtime:** Supabase only. bloommcp (egao28) keeps working unchanged.
