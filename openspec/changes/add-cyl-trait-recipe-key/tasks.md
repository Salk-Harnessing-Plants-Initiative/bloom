# Tasks: add-cyl-trait-recipe-key

## How these tasks work

**Red, then green.** Each section is red, then green. The section's tests are written and run
against the schema as the previous section left it, and the observed split is recorded before
the migration is written. "Red" includes errors such as `UndefinedFunction` and
`UndefinedColumn`. Tests that are green by design are named in each section's red-check task.

**Commits.** One commit per section, containing its tests, migration, rollback and types. Every
commit is green. Only green commits are pushed, and the PR stays a draft until §7.

**DB tests** live in `tests/integration/`. They use psycopg, connect as `supabase_admin`, and
roll back after each test. Run them with the dev stack up and `make migrate-local` applied:

```text
uv run --extra test pytest tests/integration/<file> -v
```

**Unit tests** live in `tests/unit/` and run with `uv run --extra test pytest tests/unit/<file>`.

**Two helpers from the existing tests are used throughout:**
- `_sql_body(path)` is the existing BEGIN/COMMIT-stripping helper (for example
  `tests/integration/test_cyl_experiment_traits.py:43`). It is used to re-apply a migration or
  rollback inside a test transaction.
- `_find_one("migrations", "*_<name>.sql")`, from
  `tests/integration/test_cyl_pipeline_dispatch.py:48`, locates this change's files by glob, so a
  restamp renames files only.

**Gateway tests** call through the PostgREST gateway. Each one calls `pytest.fail` instead of
skipping when the `CI` environment variable is set.

## 1. Scaffolding and review

- [x] 1.1 Created branch `eberrigan/add-cyl-trait-recipe-key` in worktree
  `.worktrees/add-cyl-trait-recipe-key` from `origin/staging`, then fast-forwarded it to
  `21487acc`, which includes PR #940.
- [x] 1.2 Read bloom#935, #937 and #936. Mapped the write-back RPC, the read RPCs and their
  consumers, the OpenSpec overlap, and contracts `identity.py`. Re-measured the staging facts
  read-only (design § Context).
- [x] 1.3 Archived `add-bulk-trait-read-rpc` (`86f791bf`). `openspec validate --specs --strict`
  passes 40/40.
- [x] 1.4 Scaffolded the proposal (`0126bf7a`).
- [x] 1.5 `openspec validate add-cyl-trait-recipe-key --strict` passes.
- [x] 1.6 First `/review-openspec` round. Fold in all blocking and important findings, then
  re-validate.
- [ ] 1.7 Second `/review-openspec` round. Fix its findings, re-validate, and commit as
  `docs(openspec): address review-openspec findings`.
- [ ] 1.8 The proposal is approved by eberrigan.

## 2. Recipe identity: helpers, columns, backfill (migration 1)

### Tests first (red)

- [ ] 2.1 **Golden vectors.**
  - Write `tests/integration/fixtures/gen_recipe_key_vectors.py`. It is not collected by pytest.
    Run it with:

    ```text
    cd bloomcli && uv run python ../tests/integration/fixtures/gen_recipe_key_vectors.py
    ```

    That uses bloomcli's `sleap-roots-contracts>=0.1.0a9` pin.
  - It builds about 20 contracts-valid `Provenance` objects covering:
    - model order;
    - duplicate triples under two `root_type` values;
    - a `root_type` change;
    - `weights_checksum` as `None` versus `""`;
    - `predict_output_params` as `None`, `{}` and non-empty;
    - code-sha changes;
    - one numeric-scale pair.
  - It writes `tests/integration/fixtures/recipe_key_v1_vectors.json`. Each entry holds
    `model_dump(mode="json")`, plus a `partition_id`: the class of `compute_idempotency_key(...)`
    with `scan_key`, `images_checksum` and `param_hash` fixed to `"X"`.
  - It also writes the Provenance field list at v0.1.0a9.
  - Commit both the script and the JSON.
- [ ] 2.2 New file `tests/integration/test_cyl_trait_recipe_key.py`, covering the helpers
  `cyl_trait_recipe_payload_v1` and `cyl_trait_recipe_key_v1`.
  - [ ] 2.2.1 `test_key_ignores_fields_outside_payload`, parametrized over every Provenance field
    outside the payload. Take the list from the vectors file, and assert it equals the list in the
    writeback spec. Mutating each field alone leaves the key unchanged. This includes each model's
    `root_type` and `sleap_nn_version`.
  - [ ] 2.2.2 `test_key_changes_with_payload_inputs`. Changing a model's `registry_id`, `version`
    or `weights_checksum` changes the key, and so does `null` versus `""`. So does changing
    either code sha, or a non-empty `predict_output_params`.
  - [ ] 2.2.3 `test_model_order_and_empty_output_params`.
  - [ ] 2.2.4 `test_duplicate_triples_counted_twice`.
  - [ ] 2.2.5 `test_odd_shapes_never_raise`. Parametrize over every input in the spec's "Odd shapes
    never raise" scenario, and assert the stated NULL and 64-hex results.
  - [ ] 2.2.6 `test_definition_hashes_to_key`, over every vector.
  - [ ] 2.2.7 `test_partitions_match_contracts_identity`. For every pair of vectors, the keys are
    equal exactly when the `partition_id`s are equal. The only exception is the marked
    numeric-scale pair, which is expected to differ.
  - [ ] 2.2.8 `test_helpers_immutable_and_granted`:
    - `provolatile = 'i'` for both helpers;
    - `has_function_privilege` is false for `anon`, and true for `bloom_agent`, `bloom_user`,
      `bloom_admin` and `authenticated`;
    - a `SET LOCAL ROLE bloom_agent` call succeeds.
- [ ] 2.3 In the same file, the schema.
  - [ ] 2.3.1 `test_columns_types_and_fks`. The five columns have the stated types and are
    nullable. Assert the FK names, targets and `confdeltype = 'n'` (`ON DELETE SET NULL`).
  - [ ] 2.3.2 `test_recipe_key_check`: every rejected and accepted value in the spec's two CHECK
    scenarios, plus `recipe_key_version = 2` rejected.
  - [ ] 2.3.3 `test_indexes_exist`, by name.
  - [ ] 2.3.4 `test_bloom_workflows_cannot_select_new_columns`. `SET LOCAL ROLE bloom_workflows`,
    then `SELECT <col>` for each of the five columns, in its own savepoint, and expect
    `InsufficientPrivilege`. The existing pin at `test_cyl_trait_source_idem_read.py:108-122`
    must still pass unchanged.
  - [ ] 2.3.5 `test_deleting_scan_or_run_nulls_the_source_link`. Seed a source with `scan_id` and
    `cyl_pipeline_run_id` set, delete the scan and the run, and assert the source remains with
    both NULL.
- [ ] 2.4 In the same file, the backfill function `cyl_backfill_trait_source_recipe_identity()`.
  - [ ] 2.4.1 `test_backfill_pipeline_source`: sets the key, version 1, and the resolved scan.
  - [ ] 2.4.2 `test_backfill_legacy_source`: sets `legacy:<id>`, version 1, and a NULL `scan_id`.
  - [ ] 2.4.3 `test_backfill_unresolvable_image_ids`, parametrized over `image_ids` that are
    missing, not an array, contain `"abc"`, match no image, or resolve to two scans.
    - The call completes, and `scan_id` stays NULL.
    - The notice count, captured with `conn.add_notice_handler`, equals the number of unresolved
      rows.
  - [ ] 2.4.4 `test_backfill_never_sets_run_stamps`.
  - [ ] 2.4.5 `test_backfill_agrees_with_trait_rows`.
  - [ ] 2.4.6 `test_backfill_is_rerunnable`: snapshot `to_jsonb` of every row, run it again, and
    compare.
  - [ ] 2.4.7 `test_backfill_not_executable_by_other_roles`.
- [ ] 2.5 **Migration and rollback.**
  - [ ] 2.5.1 `test_migration_1_body_is_idempotent`. Over seeded rows (one pipeline source, one
    legacy source), execute `_sql_body(M1)` twice in one transaction. Assert no error and
    unchanged snapshots.
  - [ ] 2.5.2 `test_rollback_1_drops_exactly_its_objects`. Apply `_sql_body(R1)`. Assert the five
    columns, two CHECKs, two FKs, three indexes, two helpers and the backfill function are gone,
    and that the a9 RPC still ingests an envelope.
  - [ ] 2.5.3 `test_rollback_1_refuses_while_stamping_body_is_live`. Apply M2 and M3, then R1.
    Assert it raises and nothing is dropped. This task becomes green only after §3 and §4, and is
    marked `xfail(strict=True)` until then.
  - [ ] 2.5.4 In the unit file `tests/unit/test_cyl_trait_recipe_migration_files.py`:
    - M1 contains `SET LOCAL lock_timeout`, ends with `NOTIFY pgrst`, and every constraint and
      index is added in its named, guarded form;
    - there is no `REFERENCES` inline in an `ADD COLUMN`;
    - no statement selects from `cyl_scan_traits`.
- [ ] 2.6 **Update the dispatch rollback test.** `test_cyl_pipeline_dispatch.py:1000`
  (`test_rollback_removes_everything`) must apply R1 before the runs-table rollback, the same way
  it already applies `RUN_EXPERIMENTS_ROLLBACK` at :1004. The new FK otherwise blocks its
  `DROP TABLE`.
- [ ] 2.7 **Record the red check.** Run 2.2–2.6 against the pre-change schema. Expected green by
  design: the unchanged idem-read pin. Everything else is expected red.

### Implementation (green)

- [ ] 2.8 Write `supabase/migrations/<T>0000_add_cyl_trait_recipe_key.sql` (design § Migration
  Plan) and `supabase/rollbacks/<T>0000_add_cyl_trait_recipe_key_rollback.sql`.
  - The rollback's header copies the hot-apply wording of the a9 rollback.
  - It includes the D4 guard, and the `migration repair` note.
- [ ] 2.9 Edit the types by hand: add the five columns to the `cyl_trait_sources`
  Row/Insert/Update in all five `database.types.ts` copies.
- [ ] 2.10 **Go green.** Run `make migrate-local`, then section 2's tests. These must still pass:
  - `test_cyl_writeback_rpc.py`, whose re-applied older bodies still insert, because every new
    column is nullable;
  - `test_cyl_trait_source_idem_read.py`;
  - `test_cyl_pipeline_dispatch.py`;
  - `test_cyl_experiment_trait_counts.py`, in particular `:973`, which deletes a scan;
  - `tests/unit/test_cyl_trait_sources_grants.py`.

## 3. Write-back stamping (migration 2)

### Tests first (red)

- [ ] 3.1 **Unit, in `tests/unit/test_cyl_trait_recipe_migration_files.py`.** Written before 3.5.
  - [ ] 3.1.1 `test_m2_differs_from_a9_only_in_stamping`. Run `difflib.ndiff` over the
    whitespace-normalized function regions of `20260928130000` and M2. Assert that the removed
    lines and the added lines are each an exact literal list:
    - the INSERT column and VALUES lines;
    - the declared variables;
    - the run lookup `SELECT`;
    - the `UPDATE … SET scan_id` statement;
    - the REVOKE line.
  - [ ] 3.1.2 `test_a9_is_the_newest_definition_before_m2`, following
    `test_cyl_writeback_a9_migration_files.py:100`, so a concurrent redefinition fails the test.
  - [ ] 3.1.3 `test_m2_calls_the_backfill` and `test_r2_restores_a9_region_verbatim`. For the
    ACL, see 3.3.
- [ ] 3.2 **Integration, in `tests/integration/test_cyl_writeback_rpc.py`.** These reuse
  `_envelope`, `_call` and `_seed_run_scan_for_writeback`. The envelopes carry realistic
  `predict_models`, and one carries none, so that the RPC path exercises the helper's odd shapes.
  - [ ] 3.2.1 `test_fresh_delivery_stamps_recipe_key_and_scan_id`.
  - [ ] 3.2.2 `test_dispatched_delivery_stamps_workflow_and_run`.
  - [ ] 3.2.3 `test_unrequested_scan_still_stamps_run`. The run-scan row count is unchanged, and
    `status_update_matched is False`.
  - [ ] 3.2.4 `test_hand_submitted_delivery_stamps_workflow_only`.
  - [ ] 3.2.5 `test_ambiguous_workflow_name_stamps_no_run`.
  - [ ] 3.2.6 `test_no_workflow_name_stamps_neither`.
  - [ ] 3.2.7 `test_noop_redelivery_leaves_stamps_unchanged`. This is a negative control, green
    before and after the change.
  - [ ] 3.2.8 `test_failed_delivery_leaves_no_source`, parametrized over an unresolvable
    `image_ids`, a non-scan-grain trait, and a non-integer blob `file_size`. Use `pytest.raises`
    inside a savepoint, then assert that no source exists for the key.
  - [ ] 3.2.9 `test_redelivery_fallback_still_works`. Keep the existing bloom#875 tests green, and
    add a stamped-source variant.
  - [ ] 3.2.10 `test_noop_stamp_guard_detects_mutation`, which proves 3.2.7 can fail. In one
    transaction:
    - read `pg_get_functiondef('insert_cyl_result_envelope(jsonb,text)'::regprocedure)`;
    - inject `UPDATE cyl_trait_sources SET argo_workflow_name = p_argo_workflow_name WHERE id =
      v_source_id;` after `v_was_noop := true;`, and `EXECUTE` it;
    - run 3.2.7's body and assert that it detects the change;
    - roll back.
  - [ ] 3.2.11 `test_source_written_between_migrations_is_backfilled`. In one transaction:
    - apply `_sql_body(R2)`, which puts the a9 body back;
    - deliver an envelope, and assert NULL columns;
    - apply `_sql_body(M2)`, and assert its `recipe_key` and `scan_id` are set.
- [ ] 3.3 **Migration and rollback.**
  - [ ] 3.3.1 `test_migration_2_body_is_idempotent`. Execute `_sql_body(M2)` twice. The overload
    arg counts are `[2]`, and a fresh delivery is stamped.
  - [ ] 3.3.2 `test_rollback_2_restores_a9_body_and_grants`. Apply `_sql_body(R2)`, then:
    - a fresh delivery leaves all five columns NULL;
    - the cross-Workflow fallback returns `status_update_matched is True`;
    - `has_function_privilege` is false for `anon` and `authenticated` and true for the four
      grantees;
    - the overload arg counts are `[2]`.
  - [ ] 3.3.3 Confirm `test_contract_migration_match.py` still reads `pinned_version = '0.1.0a9'`,
    and that `test_security_definer_grants.py` passes.
- [ ] 3.4 **Record the red check.** Run 3.1–3.3 against the §2 schema. Expected green by design:
  3.2.7, 3.2.8 and 3.2.9.

### Implementation (green)

- [ ] 3.5 Write `supabase/migrations/<T>0100_stamp_cyl_trait_source_recipe_and_run.sql`.
  - It is the `20260928130000` function region verbatim, plus the edits 3.1.1 allows.
  - It re-asserts `OWNER TO postgres`, then `REVOKE … FROM PUBLIC, anon, authenticated`, then the
    four `GRANT`s.
  - It ends with `SELECT cyl_backfill_trait_source_recipe_identity();`.
  - Write its rollback R2.
- [ ] 3.6 **Go green.** Section 3's tests, all of `test_cyl_writeback_rpc.py`,
  `test_security_definer_grants.py`, `test_contract_migration_match.py`, and 2.5.3 (still xfail
  until §4).

## 4. Recipe-aware reads (migration 3)

### Tests first (red)

- [ ] 4.1 **The fixture**, in a new file `tests/integration/test_cyl_trait_recipes_read.py`.
  - Experiment `E1` has these scans:

    | Scan | Trait data |
    |---|---|
    | `a`, `b`, `c` | `K1` only. Scan `a` has `K1` sources 10 (traits A, B) and 20 (A only, plus one NULL value) |
    | `d` | `K2` only |
    | `e` | `K1` source 20-equivalent, plus a newer `K2` source 30 |
    | `f` | `K1`, `K2` and NULL-source rows |
    | `g` | Legacy `L` only |
    | `h` | NULL-source rows only |
    | `i` | No traits |
    | `j` | A `K3` pipeline source with `scan_id` = `j` and no trait rows |

    Plus a superseded run for the `run_id_` path.
  - Experiment `E2` shares `K1` and has its own `K4`.
  - Experiment `E3` has only `L` and NULL-source rows.
  - Experiment `E4` has only NULL-source rows.
- [ ] 4.2 **`list_trait_recipes` tests.** One test per scenario in "Recipe listing for a scan
  selection", with exact `n_scans`, `recipe_kind` and the default:
  - the multi-experiment and intersection cases;
  - empty arrays for each argument;
  - both arguments NULL raises;
  - `E3`'s legacy default;
  - `E4`'s `unattributed` default;
  - `definition` hashes to its key on every pipeline row.
- [ ] 4.3 **`get_trait_recipe_coverage` tests.** One test per scenario:
  - the four statuses on `E1`;
  - `h` is `legacy_only`;
  - `j` is `other_recipe`, because `K3` contributes nothing;
  - an explicit `legacy:L` pick;
  - exact sorted `available_recipes` for `f`;
  - an all-`no_traits` selection;
  - a random 64-hex key raises;
  - the one-scan call.
- [ ] 4.4 **`get_experiment_traits` recipe-mode tests.** One test per new scenario:
  - one recipe only;
  - the highest source within a recipe (`e`);
  - no mixing within a recipe (`a`: A from 20, no B);
  - a NULL value comes back as a row;
  - `unattributed`;
  - `legacy:L` equals `source_id_ => L`;
  - a mistyped key raises;
  - all three selector pairs raise;
  - `scan_ids_` narrowing, including an empty array and another experiment's ids;
  - a recipe only in `E2` returns nothing for `E1`;
  - the `recipe_key` column in every mode.
- [ ] 4.5 **Agreement across the functions.** `test_functions_agree_on_source_of_recipe`: for
  every `included` coverage row, its `source_id` equals every row's `source_id` in the recipe
  read. §5 adds the dataset leg.
- [ ] 4.6 **Default-path parity**, in one transaction:
  1. `_sql_body(R3)`, then capture `(E1)`, `(E1, source_id_)` and `(E1, run_id_)` in function
     order;
  2. `_sql_body(M3)`, then capture the first eleven columns again;
  3. assert the two captures are equal and in the same order.
- [ ] 4.7 **Grants.**
  - `SET LOCAL ROLE` to each of `bloom_agent`, `bloom_user` and `bloom_admin`, then call all
    three functions in every mode from the spec scenario. Every call returns rows.
  - `authenticated`: check the grant.
  - `anon`: `has_function_privilege` is false.
  - `prosecdef = false` for all three.
- [ ] 4.8 **Gateway.** bloommcp's exact three-key body resolves with no PGRST203, and the rows
  carry the twelve keys. `list_trait_recipes` is reachable.
- [ ] 4.9 **Migration and rollback.**
  - [ ] 4.9.1 `test_migration_3_body_is_idempotent`: one five-argument overload after two runs.
  - [ ] 4.9.2 `test_rollback_3_restores_three_arg_function`:
    - only `(bigint, bigint, text)` exists;
    - `prosecdef`, `provolatile`, `proconfig` and `proacl` match a function freshly created from
      the `20260728000000` body in the same transaction;
    - neither new function exists;
    - re-applying M3 restores the five-argument function.
  - [ ] 4.9.3 Unit, written before 4.12:
    - `test_20260728000000_is_the_newest_get_experiment_traits_before_m3`;
    - M3 uses `DROP FUNCTION IF EXISTS`, `CREATE OR REPLACE` and `NOTIFY pgrst`;
    - the coverage and recipe SQL contain `LATERAL` with `LIMIT 1` against `cyl_scan_traits`, and
      no `EXISTS (SELECT … FROM cyl_scan_traits`.
- [ ] 4.10 **Update the existing tests that pin the old signature.** Each keeps asserting what it
  asserted before:
  - `test_cyl_experiment_traits.py:453` (`test_migration_body_is_idempotent`) and `:476`
    (`test_rollback_restores_prior_state`) start with `_sql_body(R3)` in the same transaction, so
    they still test the `20260728000000` files. Each also asserts exactly one
    `get_experiment_traits` overload.
  - `:381`: the signature string becomes `get_experiment_traits(bigint,bigint,text,text,bigint[])`.
  - `test_cyl_experiment_summary_counts.py:533` and `:548`: `pronargs` becomes 5 for
    `get_experiment_traits` only.
  - The oracle `_get_experiment_traits`, which takes three positional arguments, is unchanged
    because the defaults resolve it.

  Record each edit here.
- [ ] 4.11 **Record the red check.** Run 4.2–4.10 against the §3 schema. Expected green by design:
  4.10's oracle usage.

### Implementation (green)

- [ ] 4.12 Write `supabase/migrations/<T>0200_add_cyl_trait_recipe_reads.sql` (design D5, D6) and
  its rollback R3. Edit the three function entries in the types copies.
- [ ] 4.13 **Go green.** Section 4, plus `test_cyl_experiment_traits.py`,
  `test_cyl_experiment_summary_counts.py`, `test_cyl_read_path.py`, and 2.5.3, which now passes
  (remove its xfail).

## 5. Datasets (migration 4)

- [ ] 5.1 **Characterize today's behavior first.** New file `tests/integration/test_cyl_datasets.py`
  with `test_source_mode_characterization`, run against the §4 schema, where it must be **green**.
  It pins:
  - the timepoints filter on scans;
  - QC exclusion;
  - an unknown QC name applies no filter;
  - one source's rows only;
  - the pre-change `proacl`.

  Commit it on its own.

### Tests first (red)

- [ ] 5.2 In the same file:
  - [ ] 5.2.1 `test_frozen_rows_do_not_change`.
  - [ ] 5.2.2 `test_source_mode_records_recipe`.
  - [ ] 5.2.3 Recipe mode, one test per scenario:
    - it spans per-scan sources;
    - it uses the highest source within the recipe;
    - it leaves out other recipes;
    - `unattributed`;
    - `legacy:S` equals source mode;
    - it matches the recipe read for scans whose plant has an accession and whose experiment has a
      species.
  - [ ] 5.2.4 Add the dataset leg to 4.5's agreement test.
  - [ ] 5.2.5 `test_selector_errors`: zero selectors, two selectors or an unknown recipe each
    raise, and no row is written.
  - [ ] 5.2.6 `test_dataset_function_properties`:
    - one overload;
    - `prosecdef = false`;
    - `proconfig` contains `statement_timeout=0`;
    - `proacl` equals the characterization's.
  - [ ] 5.2.7 **Gateway.** bloomctl's five-key body with `trait_source_id: null` returns HTTP 400
    with the "exactly one selector" P0001 message, not PGRST202 or PGRST203. Nothing is written.
  - [ ] 5.2.8 `test_datasets_recipe_key_backfill_and_check`. The pipeline, legacy and NULL cases
    backfill as the spec says, a re-run changes nothing, and the CHECK accepts and rejects
    correctly.
  - [ ] 5.2.9 `test_migration_4_body_is_idempotent` and `test_rollback_4_restores_five_arg`. The
    rollback test also asserts the ACL, that the column is dropped, and the recipe-mode notice
    count.
  - [ ] 5.2.10 Unit:
    - `test_20240904033106_is_the_newest_create_cyl_dataset_before_m4`;
    - M4 has `lock_timeout`, `DROP … IF EXISTS`, `CREATE OR REPLACE`, `NOTIFY pgrst`, and
      `SET statement_timeout TO '0'`;
    - M4 does not contain `alter database` or `alter role`.
- [ ] 5.3 **Record the red check** against the §4 schema. Expected green by design: 5.1 and 5.2.1.

### Implementation (green)

- [ ] 5.4 Write `supabase/migrations/<T>0300_add_cyl_dataset_recipe_mode.sql` and R4. Edit the
  `cyl_datasets` and `create_cyl_dataset` entries in the types copies.
- [ ] 5.5 **Go green.** Section 5, plus `bloomcli/tests/test_cyl_datasets.py`, which is mocked and
  unchanged.
- [ ] 5.6 **The full reverse chain**, `test_full_rollback_chain_round_trip`, in one transaction:
  1. apply R4, R3, R2 and R1;
  2. assert an a9 delivery works, as do the three-argument read and the five-argument dataset
     function, and that no new column, index or function remains;
  3. re-apply M1–M4 and assert the new state.

## 6. Types and docs

- [ ] 6.1 **Types.**
  - Confirm the four generated `database.types.ts` copies are byte-identical: run `sha256sum` on
    `web/lib/database.types.ts` and the three `packages/*/…/database.types.ts` copies.
  - Confirm that a `make gen-types` diff, restricted to the entries this change touched, matches
    the hand edits.
  - Confirm `web/types/database.types.ts` (hand-maintained) has the same entries.
- [ ] 6.2 **Sidecar files.**
  - Write `_WIKI/SUPABASE/trait-recipes.export.schema.json` (draft 2020-12) and
    `_WIKI/SUPABASE/trait-recipes.export.example.json`.
  - Tests first, in unit file `tests/unit/test_trait_recipe_export_schema.py`:
    - the schema parses;
    - the example has every `required` property at every level;
    - every schema property appears in the page's field table (6.3);
    - every table source column exists in M3's `RETURNS TABLE` lists or in `cyl_trait_sources`,
      or is marked producer-supplied.
- [ ] 6.3 **`_WIKI/SUPABASE/trait-recipes.md`**, the reader-facing page. It links to the spec
  requirements for the rules rather than restating them, and covers:
  - latest vs default recipe;
  - recipe vs idempotency key vs source, with a pointer to `list_experiment_trait_sources`;
  - the pseudo-recipes;
  - `legacy_only` also covering unattributed-only scans;
  - one `get_experiment_traits` call per experiment for a multi-experiment export;
  - the dataset recipe-mode call shape (`trait_source_id: null`);
  - the blind spot tracked by contracts#45;
  - the sidecar field table.
- [ ] 6.4 **Update the existing docs:**
  - `_WIKI/BLOOMMCP/README.md:147-184`:
    - the five-argument signature;
    - one source per scan for pipeline data;
    - recommend `list_trait_recipes` and `recipe_key_` over pinning one source;
    - a link to the page.
  - `_WIKI/SUPABASE/README.md`:
    - under § Write-back RPC, the five stamps and "a no-op never rewrites them";
    - under § Pipeline-trigger tables, the new index and the `cyl_pipeline_run_id` FK;
    - a link to the page.
  - `_WIKI/README.md`: add the new files to the layout tree.
  - `contracts/README.md` § Re-pin procedure: add a step. If a keyed Provenance field changes,
    decide on `recipe_key_version = 2` (link the page). Also update the a3 note at :92.
  - `services/workflows/README.md:182`: note that this response's `pipeline_run_id` is the value
    `cyl_trait_sources.cyl_pipeline_run_id` stores.
  - `bloomcli/README.md:83-84`: note that a pipeline source name selects one scan's rows, and that
    per-recipe datasets come with #481.
- [ ] 6.5 Run `make erd` and commit `_WIKI/SUPABASE/erd.md`. Regenerate it on any rebase; don't
  hand-merge it.

## 7. Pre-merge

- [ ] 7.1 **`/pre-merge`, plus the gaps it does not cover:**
  - `uvx ruff@0.9.9 check tests/` and `uvx ruff@0.9.9 format --check` on the new test files;
  - `uv run pre-commit run --files <every changed file>`;
  - the full `tests/unit/`;
  - the full `tests/integration/` with the stack up;
  - `bloomcli`'s `tests/test_cyl_datasets.py`;
  - `scripts/lint_migrations.sh origin/staging`;
  - `scripts/lint_migration_isolation.py`;
  - `openspec validate add-cyl-trait-recipe-key --strict` and `openspec validate --specs --strict`;
  - a manual check that no other active change MODIFIES a requirement this change modifies.
- [ ] 7.2 **Pre-merge dry run on staging, read-only.** Run the backfill function's SELECT logic as
  CTEs, with the helper inlined. Record:
  - 85/85 keys;
  - 80/80 `scan_id`s;
  - 10 distinct pipeline keys;
  - backfilled `scan_id` agreeing with the trait rows for 79 of 79;
  - no errors.
- [ ] 7.3 **Coverage timing on staging, read-only (manual, not CI).** Run the coverage probe SQL
  for experiment 1 as a CTE under `EXPLAIN ANALYZE`. It must be well under 8 s; 450 ms was
  measured on 2026-09-29.
- [ ] 7.4 **Timestamps.** Fetch `origin/staging`. If a newer migration has landed, restamp all
  four files and their rollbacks with `git mv`, keeping their order. Repair the dev DB with
  `supabase migration repair --status reverted <old>`, and re-run the suite.
- [ ] 7.5 **PR body.**
  - Write it with `/pr-description`.
  - Its **Schema changes** section is generated with `make erd-snapshot CHANGED=origin/staging`.
    It lists `cyl_trait_sources`, `cyl_datasets` and `cyl_pipeline_run_scans`, and every named
    constraint and index.
  - It says "Part of #935, #937", not "Closes".
  - It gives a per-commit review order: §2–§3 for Benfica (@blm3886), §4 for egao28.
  - It notes the `erd.md` and timestamp overlap with Benfica's video-queue branch.
  - Check it with `make pr-body-check BODY=<file>`.
- [ ] 7.6 Open the PR to `staging` **only after eberrigan says yes**, to be squash-merged. Do not
  merge it; eberrigan merges.

## 8. After merge

- [ ] 8.0 **Before promoting staging to main:** repeat 7.2's dry run read-only on prod.
- [ ] 8.1 **Read-only staging checks after deploy:**
  - 85/85 keys, 80/80 `scan_id`s, 10 distinct pipeline keys;
  - `list_trait_recipes(ARRAY[12880747])` (the A4 pipeline E2E experiment) defaults to the a9
    recipe;
  - coverage for experiment 1 completes under 8 s through PostgREST.
- [ ] 8.2 On the first Bloom-dispatched run after deploy, confirm the new source's stamps. If no
  such run has happened, record this as blocked.
- [ ] 8.3 **Drafts for eberrigan to approve before anything is posted:**
  - a note on #936 for egao28, naming the new arguments, "one recipe per frame", and the bloommcp
    docs that still describe one source per frame (`bloommcp/docs/data-access-roadmap.md:276`,
    `bloommcp/docs/storage-backends.md:62-70`);
  - notes on #865, #481 and #482;
  - the recipe-retirement follow-up issue (design D10).
- [ ] 8.4 After 8.1, close #935 and #937 **with eberrigan's yes**.
- [ ] 8.5 After the staging-to-main promotion is verified, `/openspec:archive add-cyl-trait-recipe-key`.
