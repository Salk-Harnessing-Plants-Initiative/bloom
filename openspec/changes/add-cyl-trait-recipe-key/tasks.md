# Tasks: add-cyl-trait-recipe-key

Every section uses red, then green. Its tests are written and run against the **unmodified**
schema first, and the observed red/green split is recorded before any migration is written.

**DB tests** are Python and psycopg in `tests/integration/`. They connect as `supabase_admin` and
each test rolls back. Run them with the dev stack up and `make migrate-local` applied:

```text
uv run --extra test pytest tests/integration/<file> -v
```

## 1. Scaffolding

- [x] 1.1 Branch `eberrigan/add-cyl-trait-recipe-key` created in worktree
  `.worktrees/add-cyl-trait-recipe-key` from `origin/staging` (`9eb81a95`), then fast-forwarded
  to `21487acc` after PR #940 merged. The main checkout is untouched.
- [x] 1.2 Read bloom#935, #937 and #936. Mapped the write-back RPC, the read RPCs and their
  consumers, the OpenSpec overlap, and contracts `identity.py`. Re-measured the staging facts
  read-only (design.md § Context).
- [x] 1.3 Archived `add-bulk-trait-read-rpc` into `cyl-trait-read` (commit `86f791bf`). Its
  duplicate "Bulk read grants…" header was renamed, and task 7.10 was recorded as not applied.
  `openspec validate --specs --strict` passes 40/40.
- [x] 1.4 Scaffolded proposal.md, design.md, this file, and deltas for `cyl-trait-writeback`,
  `cyl-trait-read` and `cyl-datasets`.
- [x] 1.5 `openspec validate add-cyl-trait-recipe-key --strict` passes.
- [ ] 1.6 `/review-openspec` findings fixed, and the user approves the proposal.

## 2. Recipe identity schema and backfill (migration 1)

### Tests first (red)

- [ ] 2.1 New file `tests/integration/test_cyl_trait_recipe_key.py`: the helper
  `cyl_trait_recipe_key_v1`.
  - [ ] 2.1.1 **Per-scan invariance.** Two Provenance dicts that differ only in `scan_key`,
    `inputs`, `params`, `contract_version`, digests or `predict_inference_config` give the same
    key.
  - [ ] 2.1.2 **Sensitivity.** Changing any model triple, either code sha, or a non-empty
    `predict_output_params` changes the key.
  - [ ] 2.1.3 **Order and empty params.** Models in any order give one key. So do `null`, `{}`
    and a missing `predict_output_params`.
  - [ ] 2.1.4 **`root_type` excluded.** Two models with the same triples but different
    `root_type` give the same key.
  - [ ] 2.1.5 **Duplicate triples.** A model serving two root types gives a different key from
    the same model listed once. This mirrors `identity.py`, which keeps duplicates.
  - [ ] 2.1.6 **Shape and NULL input.** NULL input gives NULL. The output matches
    `^[0-9a-f]{64}$`.
  - [ ] 2.1.7 **`IMMUTABLE`, and no `EXECUTE` for the public roles.** `provolatile = 'i'`, and
    `has_function_privilege` is false for `anon` and `authenticated`.
- [ ] 2.2 Same file, schema tests.
  - [ ] 2.2.1 The five columns exist with the types in the spec, nullable, with FKs to
    `cyl_scans` and `cyl_pipeline_runs`.
  - [ ] 2.2.2 The CHECKs reject a malformed `recipe_key` (uppercase hex, 63 characters,
    `legacy:x`) and `recipe_key_version = 2`.
  - [ ] 2.2.3 The three indexes exist.
  - [ ] 2.2.4 `bloom_workflows` cannot SELECT any new column. Assert this alongside the existing
    `test_cyl_trait_source_idem_read.py:108-122` column pin, which must still pass unchanged.
- [ ] 2.3 Same file, backfill tests. They seed rows with the columns NULLed, then run the
  migration's backfill statements.
  - [ ] 2.3.1 A pipeline source gets `recipe_key = helper(metadata)`, version 1, and the scan
    its `image_ids` resolve to.
  - [ ] 2.3.2 A legacy source (NULL `metadata`) gets `legacy:<id>`, version 1 and a NULL
    `scan_id`.
  - [ ] 2.3.3 A source whose `image_ids` resolve to 0 or 2 scans keeps a NULL `scan_id`.
  - [ ] 2.3.4 `argo_workflow_name` and `cyl_pipeline_run_id` stay NULL.
  - [ ] 2.3.5 Re-running the backfill changes nothing.
  - [ ] 2.3.6 The backfilled `scan_id` equals the scan of every trait row of that source.
- [ ] 2.4 Same file, the `cyl_datasets.recipe_key` column and its backfill from
  `trait_source_id`.
- [ ] 2.5 Migration and rollback tests.
  - [ ] 2.5.1 The migration body re-applies without error, which `test_migrations.py`'s
    `db push` idempotency covers.
  - [ ] 2.5.2 The rollback drops exactly the five columns, their constraints and indexes, the
    helper, and `cyl_datasets.recipe_key`.
- [ ] 2.6 Run 2.1–2.5 against the unmodified schema and record the red/green split (expected:
  all red).

### Implementation (green)

- [ ] 2.7 Write `supabase/migrations/<ts>_add_cyl_trait_recipe_key.sql`.
  - `<ts>` must be greater than staging's newest migration at authoring time (`20260929204846`
    on 2026-09-29). Re-check before pushing.
  - Contents: the helper, the columns, CHECKs, FKs and indexes (using the guarded forms in
    `.claude/commands/database-migration.md`), the D4 backfill with a `RAISE NOTICE` of unresolved
    counts, `cyl_datasets.recipe_key` and its backfill, and `BEGIN;`/`COMMIT;`.
  - Write the companion `supabase/rollbacks/<ts>_add_cyl_trait_recipe_key_rollback.sql`.
- [ ] 2.8 `make migrate-local`. Section 2's tests go green. The existing
  `test_cyl_writeback_rpc.py`, `test_cyl_trait_source_idem_read.py` and
  `tests/unit/test_cyl_trait_sources_grants.py` still pass.

## 3. Write-back stamping (migration 2)

### Tests first (red)

- [ ] 3.1 Add tests to `tests/integration/test_cyl_writeback_rpc.py`, reusing `_envelope`, `_call`
  and `_seed_run_scan_for_writeback`.
  - [ ] 3.1.1 `test_fresh_delivery_stamps_recipe_key_and_scan_id`: the stored `recipe_key`
    equals `helper(metadata)`, the version is 1, and `scan_id` equals the returned `scan_id`.
  - [ ] 3.1.2 `test_dispatched_delivery_stamps_workflow_and_run`: seed run R with a row for W.
    The source gets `argo_workflow_name = W` and `cyl_pipeline_run_id = R`.
  - [ ] 3.1.3 `test_unrequested_scan_still_stamps_run`: R has a W row for a different scan. The
    source gets `cyl_pipeline_run_id = R`. The number of `cyl_pipeline_run_scans` rows is
    unchanged, and `status_update_matched is False`.
  - [ ] 3.1.4 `test_hand_submitted_delivery_stamps_workflow_only`: no row carries W, so the run
    is NULL.
  - [ ] 3.1.5 `test_ambiguous_workflow_name_stamps_no_run`: W appears in two runs, so the run is
    NULL.
  - [ ] 3.1.6 `test_no_workflow_name_stamps_neither`: `recipe_key` and `scan_id` are still set.
  - [ ] 3.1.7 `test_noop_redelivery_leaves_stamps_unchanged`: re-deliver under W2 and all five
    columns are unchanged. This is a negative control: it must pass both before and after the new
    RPC body.
  - [ ] 3.1.8 `test_failed_delivery_leaves_no_stamped_source`: an unresolvable `image_ids` leaves
    no row for that key.
- [ ] 3.2 Keep `insert_cyl_result_envelope`'s signature at exactly one overload with
  `pronargs = 2`. The existing tests (`:1535-1539`, `:1686`, `:1710`) cover this.
  `test_contract_migration_match.py` still reads `pinned_version = '0.1.0a9'`.
- [ ] 3.3 Run 3.1 against the section-2 schema and record the split (expected: 3.1.1–3.1.6 red,
  3.1.7 and 3.1.8 green).

### Implementation (green)

- [ ] 3.4 Write `supabase/migrations/<ts2>_stamp_cyl_trait_source_recipe_and_run.sql`.
  - `CREATE OR REPLACE insert_cyl_result_envelope(jsonb, text)`. **Copy the body verbatim from
    `20260928130000`**, then apply exactly two edits:
    - (a) the `INSERT` at :127-130 gains `recipe_key`, `recipe_key_version`,
      `argo_workflow_name` and `cyl_pipeline_run_id`;
    - (b) one `UPDATE … SET scan_id` right after scan resolution (:228).
  - Re-assert `OWNER TO postgres`, `REVOKE … FROM PUBLIC, anon, authenticated`, and the grants
    to `bloom_writer, service_role, bloom_admin, bloom_workflows`.
  - Write the rollback, which restores the `20260928130000` body and grants.
- [ ] 3.5 Add a test that diffs the new body against `20260928130000`'s. It normalizes
  whitespace and allows only the two edits, following the pattern in
  `tests/unit/test_cyl_writeback_a9_migration_files.py`. This guards against a silent regression
  of the bloom#875 fallback.
- [ ] 3.6 Green: section 3, the whole of `test_cyl_writeback_rpc.py`,
  `test_security_definer_grants.py` and `test_contract_migration_match.py`.
- [ ] 3.7 **Negative-control check for 3.1.7.** Temporarily make the no-op branch update
  `argo_workflow_name` in the applied function and confirm that 3.1.7 alone goes red. Restore it
  with `make migrate-local`, and record the result here.

## 4. Recipe-aware reads (migration 3)

### Tests first (red)

- [ ] 4.1 New file `tests/integration/test_cyl_trait_recipes_read.py`, with one fixture
  experiment:
  - scans under K1 (two with 2 K1 sources each);
  - one scan under K2 only;
  - one scan with K1 and a newer K2;
  - one scan with legacy source L only;
  - one scan with NULL-source rows only;
  - one scan with no traits;
  - a second experiment, for boundary checks.
  - [ ] 4.1.1 `list_trait_recipes`: the rows, `n_scans`, `recipe_kind` and `definition` shape;
    the default is the highest `newest_source_id`, not the most scans; `unattributed` ranks
    last; a scan-only selection; an empty selection gives zero rows; both arguments NULL
    raises.
  - [ ] 4.1.2 `get_trait_recipe_coverage`: all four statuses against the default; an explicit
    `legacy:L` pick; `source_id` matches the recipe read; `available_recipes` is exact; an
    unknown key raises; a one-scan call.
  - [ ] 4.1.3 `get_experiment_traits` recipe mode:
    - one recipe only;
    - the newest source within the recipe;
    - `unattributed`;
    - `scan_ids_` narrowing (including an empty array, and another experiment's scan ids);
    - any two selectors raise;
    - the `recipe_key` column on every mode.
  - [ ] 4.1.4 **Default-path parity.** Before migrating, capture
    `get_experiment_traits(E)`, `(E, source_id_)` and `(E, run_id_)` on the fixture. Assert that
    the first 11 columns are identical after migrating.
  - [ ] 4.1.5 Grants: the four read roles can execute all three functions; `anon` cannot;
    `SECURITY INVOKER`.
  - [ ] 4.1.6 **PostgREST** (skipped without the gateway): bloommcp's exact named 3-key body
    resolves with no PGRST203, and `list_trait_recipes` is reachable.
- [ ] 4.2 **Update the existing tests that pin the old signature:**
  - `tests/integration/test_cyl_experiment_traits.py:381-382` (`has_function_privilege` with the
    3-arg signature), `:453-473` (re-applying the old body, `pronargs = 3`) and `:476-499`
    (rollback);
  - `tests/integration/test_cyl_experiment_summary_counts.py:533` and `:548` (`pronargs = 3`).

  Point them at the 5-arg signature, and keep what each test was asserting. Record what changed
  in each one.
- [ ] 4.3 Run 4.1 and 4.2 against the section-3 schema and record the split.

### Implementation (green)

- [ ] 4.4 Write `supabase/migrations/<ts3>_add_cyl_trait_recipe_reads.sql`.
  - `DROP FUNCTION get_experiment_traits(bigint, bigint, text)`, then create the 5-argument
    function. With the new arguments NULL, its body is the old body.
  - Create `list_trait_recipes` and `get_trait_recipe_coverage`. Their legacy and
    `unattributed` probes use the `LATERAL … LIMIT 1` form (design D5).
  - Apply `REVOKE … FROM PUBLIC, anon` and the four `GRANT`s, then `NOTIFY pgrst, 'reload
    schema'`.
  - Write the rollback, which restores the `20260728000000` function and grants.
- [ ] 4.5 Green: section 4 plus `test_cyl_experiment_traits.py`,
  `test_cyl_experiment_summary_counts.py` and `test_cyl_read_path.py`.
- [ ] 4.6 **Performance check on staging, read-only.** Run the legacy-probe part of
  `get_trait_recipe_coverage` as a CTE (the functions don't exist there yet) for experiment 1
  under `EXPLAIN ANALYZE`. Record the time. It must stay well under 8 s; 450 ms was measured on
  2026-09-29.

## 5. Datasets (migration 4)

### Tests first (red)

- [ ] 5.1 New file `tests/integration/test_cyl_datasets.py`. No DB test of `create_cyl_dataset`
  exists today. Tests:
  - [ ] 5.1.1 **Characterization of today's source mode**, written and run *before* any change so
    it pins the current behavior: timepoints filter, QC exclusion, and one source's rows only.
  - [ ] 5.1.2 Source mode stores `recipe_key` equal to the source's key.
  - [ ] 5.1.3 Recipe mode spans per-scan sources; takes the newest source within the recipe;
    leaves out other recipes; supports `unattributed`; and matches `get_experiment_traits`'
    recipe read for scans whose plant has an accession.
  - [ ] 5.1.4 Zero selectors, two selectors or an unknown recipe raises, and no row is written.
  - [ ] 5.1.5 Exactly one overload; `SECURITY INVOKER`; `proconfig` contains
    `statement_timeout=0`. PostgREST (skipped without the gateway): bloomctl's named 5-key body
    resolves.
  - [ ] 5.1.6 The rollback restores the 5-arg function and drops the column.
- [ ] 5.2 Run 5.1 against the section-4 schema and record the split (expected: 5.1.1 green,
  the rest red).

### Implementation (green)

- [ ] 5.3 Write `supabase/migrations/<ts4>_add_cyl_dataset_recipe_mode.sql`: `DROP FUNCTION
  create_cyl_dataset(text, bigint, bigint, json, json)`, then create the 6-argument function, with
  a `SET statement_timeout TO '0'` clause. Write its rollback.
- [ ] 5.4 Green: section 5. bloomcli's mocked `test_cyl_datasets.py` is unaffected; confirm that
  by running it.

## 6. Types and docs

- [ ] 6.1 **Types: targeted hand edits, not a bulk `make gen-types`.** A bulk regeneration drags
  in unrelated drift. Edit these entries:
  - the `cyl_trait_sources` Row/Insert/Update;
  - `cyl_datasets`;
  - `get_experiment_traits`, `list_trait_recipes`, `get_trait_recipe_coverage` and
    `create_cyl_dataset`.

  Edit them in `web/lib/database.types.ts` and its three byte-identical copies
  (`packages/bloom-js`, `packages/bloom-fs`, `packages/bloom-nextjs-auth`), and in the
  hand-maintained `web/types/database.types.ts`. Confirm the four generated copies stay
  byte-identical, and that a `make gen-types` diff limited to these entries matches.
- [ ] 6.2 Write `_WIKI/SUPABASE/trait-recipes.md`. It covers:
  - what a recipe is, and how it differs from the idempotency key;
  - the v1 definition, and what is recorded but not keyed;
  - the pseudo-recipes;
  - "newest";
  - the coverage statuses;
  - the known blind spot (contracts#45);
  - export sidecar v1: three files, and a field table that maps every field to its RPC column or
    marks it producer-supplied.
- [ ] 6.3 Update `_WIKI/SUPABASE/README.md`, covering the new columns and RPCs, and
  `_WIKI/SUPABASE/erd.md` via `make erd`.

## 7. Pre-merge

- [ ] 7.1 `/pre-merge`: lint, the full unit and integration suites, the migration lints
  (`lint_migrations.sh` and `lint_migration_isolation.py`), and `openspec validate --strict` for
  the change and for all specs.
- [ ] 7.2 Write the PR body with `/pr-description`, including the **Schema changes** section (ER
  snapshot and constraints table), and check it with `make pr-body-check BODY=<file>`.
- [ ] 7.3 Open the PR to `staging` **only after the user says yes**. Do not merge it; the user
  merges.

## 8. After merge (staging)

- [ ] 8.1 **Read-only staging checks after deploy:**
  - 85/85 sources have a `recipe_key`;
  - 80/80 pipeline sources have a `scan_id`;
  - 10 distinct pipeline keys, with the 15 a9 sources as one;
  - `list_trait_recipes(ARRAY[12880747])` defaults to the a9 recipe;
  - `get_trait_recipe_coverage` for experiment 1 completes under 8 s through PostgREST.
- [ ] 8.2 On the first Bloom-dispatched run after deploy, confirm the new source's
  `argo_workflow_name` and `cyl_pipeline_run_id` are set. That needs a real run; if none has
  happened, record it as blocked.
- [ ] 8.3 **Drafts for the user to approve before anything is posted:**
  - a note on #936 for Evelyn: the new arguments, and "one recipe per frame" as the durable path;
  - notes on #865, #481 and #482;
  - a follow-up issue for recipe retirement (design D10).
- [ ] 8.4 After the staging→main promotion is verified, `/openspec:archive add-cyl-trait-recipe-key`.
