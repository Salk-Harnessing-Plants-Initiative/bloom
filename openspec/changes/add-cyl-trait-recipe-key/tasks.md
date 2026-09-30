# Tasks: add-cyl-trait-recipe-key

## How these tasks work

**Red, then green.**

- Each section's tests are written first and run against the schema as the previous section left
  it.
- The observed split is recorded before the section's migration is written. "Red" includes
  errors such as `UndefinedFunction` or `UndefinedColumn`.
- Each red-check task names the tests that are green by design.

**Commits and PR.**

- One commit per section: its tests, migration, rollback, types and checkbox ticks. Every commit
  is green locally.
- No PR exists until 7.6. After review starts, fixes are added as new commits; history is not
  rewritten.

**DB tests** are in `tests/integration/`. They use psycopg, connect as `supabase_admin`, and roll
back after each test. Run them with the dev stack up and `make migrate-local` applied:

```text
uv run --extra test pytest tests/integration/<file> -v
```

**Unit tests** are in `tests/unit/`.

**Shared test helpers** live in `tests/integration/cyl_recipe_helpers.py` (not collected; imported
by the test files):

- `_sql_body(path)` (as at `test_cyl_experiment_traits.py:43`) re-applies a migration or
  rollback inside a test transaction.
- `_find_one("migrations"|"rollbacks", glob)` (as at `test_cyl_pipeline_dispatch.py:48`) locates
  this change's files by glob, so a restamp only renames files.
- `_apply_recipe_rollbacks(cur, down_to=1)` applies whichever of R4, R3, R2 and R1 exist, newest
  first, stopping at `down_to`. This follows `test_scrna_de_contrast.py:742`. Every test that needs
  a pre-change state uses it, so each commit stays green as later rollbacks appear.
- `_acl_set(cur, signature)` returns the set of `(grantee, privilege_type)` from
  `aclexplode(proacl)`. `grantee = 0` is `PUBLIC`. ACLs are compared as sets, never as raw
  `aclitem[]`.
- `_seed_source(cur, metadata, …)` inserts directly into `cyl_trait_sources (name, metadata,
idempotency_key)` as `supabase_admin`, leaving the recipe and run columns NULL. Backfill tests
  seed this way, because once M2 is live an RPC delivery arrives already stamped.

**Test data.** Workflow names and dataset names are `uuid4`-based, because the dev database holds
committed rows from other runs.

**Gateway tests** go through PostgREST. They use `pytest.fail` instead of skipping when `CI` is
set, and they assert only status codes and error codes, because test-transaction data is
invisible to PostgREST.

## 1. Scaffolding and review

- [x] 1.1 Created the worktree `.worktrees/add-cyl-trait-recipe-key` on branch
      `eberrigan/add-cyl-trait-recipe-key`, from `origin/staging` fast-forwarded to `21487acc`, which
      includes PR #940.
- [x] 1.2 Read bloom#935, #937 and #936. Mapped the code and the OpenSpec overlap, and re-measured
      staging read-only (design § Context).
- [x] 1.3 Archived `add-bulk-trait-read-rpc` (`86f791bf`).
- [x] 1.4 Scaffolded the proposal (`0126bf7a`).
- [x] 1.5 `openspec validate add-cyl-trait-recipe-key --strict` passes.
- [x] 1.6 First `/review-openspec` round: fixes folded in (`150640b9`).
- [x] 1.7 Second round: fixes folded in, validated, and committed as
      `docs(openspec): address second review-openspec round`.
- [x] 1.8 The proposal is approved by eberrigan (2026-09-29).

## 2. Recipe identity: helpers, columns and backfill (migration 1)

### Tests first (red)

- [x] 2.1 **Golden vectors.**

  - Write `tests/integration/fixtures/gen_recipe_key_vectors.py`. It is not collected, because
    pytest collects only `test_*.py`.
  - Run it as:

    ```text
    cd bloomcli && uv run --frozen python ../tests/integration/fixtures/gen_recipe_key_vectors.py
    ```

  - The script asserts `importlib.metadata.version("sleap-roots-contracts") == "0.1.0a9"`, and
    records that version in the output.
  - It builds about 20 contracts-valid `Provenance` objects:
    - model order;
    - duplicate triples under two `root_type` values;
    - a `root_type` change;
    - `weights_checksum` `None` versus `""`;
    - `predict_output_params` as `None`, `{}` and non-empty;
    - code-sha changes.
  - Each vector stores `raw_provenance`, a **JSON text string** of `model_dump(mode="json")`,
    plus a `partition_id`. The `partition_id` is the class of `compute_idempotency_key(...)` with
    `scan_key`, `images_checksum` and `param_hash` fixed to `"X"`.
  - One pair is written by hand as raw text: `{"peak_threshold": 1}` versus
    `{"peak_threshold": 1.0}`. Contracts puts them in the same partition, and the pair is marked
    `expected_divergence`.
  - The script writes `json.dumps(…, indent=2) + "\n"` to
    `tests/integration/fixtures/recipe_key_v1_vectors.json`, and it has a `--check` mode that
    verifies a byte-identical regeneration.
  - Commit both files.

- [x] 2.2 New file `tests/integration/test_cyl_trait_recipe_key.py`: the helpers.
  - **Passing vectors in.** Every vector is passed to Postgres as `%s::jsonb` from
    `raw_provenance` text, never through `json.loads` or `Jsonb`.
  - [x] 2.2.1 `test_key_ignores_fields_outside_payload`, parametrized over a literal
        `EXCLUDED_FIELDS` constant that mirrors the writeback spec's list: the top-level fields, plus
        `predict_models[].root_type` and `predict_models[].sleap_nn_version`. The test also asserts
        that the vectors file's field list minus the four payload fields equals the top-level part of
        `EXCLUDED_FIELDS`.
  - [x] 2.2.2 `test_key_changes_with_payload_inputs`. Covers each triple field, `null` versus
        `""`, both code shas, and a non-empty `predict_output_params`.
  - [x] 2.2.3 `test_model_order_and_empty_output_params`. `predict_output_params` as `null`,
        `{}` and absent gives one key.
  - [x] 2.2.4 `test_duplicate_triples_counted_twice`.
  - [x] 2.2.5 `test_odd_shapes_never_raise`, over every input in the spec scenario. It covers the
        NULL results for `NULL`, `'[]'` and `'"x"'`.
  - [x] 2.2.6 `test_definition_hashes_to_key`, over every vector.
  - [x] 2.2.7 `test_partitions_match_contracts_identity`.
    - For each pair of vectors, the keys are equal exactly when the `partition_id`s are, except
      for the `expected_divergence` pair, whose keys differ.
    - Also assert that `'{"peak_threshold": 1.0}'::jsonb::text` contains `1.0`, so the pair really
      reaches Postgres distinct.
  - [x] 2.2.8 `test_helpers_immutable_owned_and_granted`.
    - Both helpers have `provolatile = 'i'` and owner `postgres`.
    - `has_function_privilege` is false for `anon`, and true for `bloom_agent`, `bloom_user`,
      `bloom_admin` and `authenticated`.
    - A `SET LOCAL ROLE bloom_agent` call succeeds.
- [x] 2.3 Same file: schema tests.
  - [x] 2.3.1 `test_columns_types_and_fks`: column types, nullability, FK names, targets, and
        `confdeltype = 'n'`.
  - [x] 2.3.2 `test_recipe_key_checks`: every value in the spec's three CHECK scenarios.
  - [x] 2.3.3 `test_indexes_exist`, by name.
  - [x] 2.3.4 `test_bloom_workflows_cannot_select_new_columns`.
    - `SET LOCAL ROLE bloom_workflows`, then `SELECT` each of the five columns, each in its own
      savepoint. Each fails with `InsufficientPrivilege`.
    - The pin at `test_cyl_trait_source_idem_read.py:108-122` still passes unchanged.
  - [x] 2.3.5 `test_deleting_scan_or_run_nulls_the_source_link`.
    - Seed a scan with no trait rows, and a run with no run-scan rows.
    - Link a source to both.
    - Delete the scan and the run; the source remains, with both columns NULL.
- [x] 2.4 Same file: the backfill function. Seed every case with `_seed_source`, and assert its
      columns are NULL before the call.
  - [x] 2.4.1 `test_backfill_pipeline_source`.
  - [x] 2.4.2 `test_backfill_legacy_and_non_object_metadata`: metadata `NULL`, `'[]'` and
        JSON `null` each give `legacy:<id>`, version 1 and a NULL `scan_id`.
  - [x] 2.4.3 `test_backfill_unresolvable_image_ids`.
    - Parametrized over `image_ids` that are missing, not an array, `[]`, containing `"abc"`,
      matching no image, or resolving to two scans.
    - The call completes and `scan_id` stays NULL.
    - The NOTICE, captured with `conn.add_notice_handler`, matches
      `cyl recipe backfill: (\d+) source\(s\) with unresolved image_ids`. Its count equals the
      baseline plus the number seeded, where the baseline is the count of object-metadata sources
      with NULL `scan_id` taken before seeding.
    - A resolvable duplicate id `[i, i]` does resolve.
  - [x] 2.4.4 `test_backfill_never_sets_run_stamps`.
  - [x] 2.4.5 `test_backfill_agrees_with_rpc_written_trait_rows`.
    - Deliver an envelope through the RPC, NULL that source's columns, and backfill.
    - The `scan_id` equals the scan of its trait rows.
  - [x] 2.4.6 `test_backfill_is_rerunnable`: `to_jsonb` snapshots before and after a second call
        are equal.
  - [x] 2.4.7 `test_backfill_owner_and_grants`.
    - The owner is `postgres`.
    - `has_function_privilege` is false for `anon`, `authenticated`, `service_role`,
      `bloom_agent`, `bloom_user`, `bloom_admin`, `bloom_writer` and `bloom_workflows`.
    - Superusers are excluded from the check.
- [x] 2.5 **Migration and rollback.**
  - [x] 2.5.1 `test_migration_1_body_is_idempotent`. Execute `_sql_body(M1)` twice over `_seed_source`
        rows (one pipeline, one legacy). There are no errors, the snapshots are unchanged, and the
        `insert_cyl_result_envelope` overload arg counts are `[2]`.
  - [x] 2.5.2 `test_rollback_1_drops_exactly_its_objects`.
    - Run `_apply_recipe_rollbacks(cur, down_to=1)`.
    - Assert all of these are gone: the recipe and run columns, the two CHECKs, the two FKs, the
      three indexes, the two helpers and the backfill function.
    - Assert that an a9 envelope ingests.
  - [x] 2.5.3 `test_rollback_1_guard_detects_a_referencing_function`.
    - Create `public._r1_probe()`, a plpgsql function that selects `recipe_key` from
      `cyl_trait_sources`.
    - Apply only R1. It raises, naming `_r1_probe`, and nothing has been dropped (checked in a
      savepoint).
  - [x] 2.5.4 In a new unit file, `tests/unit/test_cyl_trait_recipe_migration_files.py`, check M1:
    - it has `SET LOCAL lock_timeout` and ends with `NOTIFY pgrst`;
    - every constraint and index is added in its named, guarded form;
    - no `ADD COLUMN` has an inline `REFERENCES` or `CHECK`;
    - it has no `SELECT` from `cyl_scan_traits`;
    - every created function is followed by `OWNER TO postgres`;
    - every `UPDATE` has a `WHERE`;
    - R1's guard `DO` block comes before every `DROP` and `ALTER`.
- [x] 2.6 **Dispatch rollback test.** Edit `test_cyl_pipeline_dispatch.py:1000`
      (`test_rollback_removes_everything`) to run `_apply_recipe_rollbacks(cur, down_to=1)` before
      `RUN_EXPERIMENTS_ROLLBACK` (:1004). Otherwise the new FK blocks the `DROP TABLE` at
      `20260730120000_create_cyl_pipeline_runs_rollback.sql:57-58`.
- [x] 2.7 **Record the red check.** Run 2.2–2.6 against the pre-change schema. Expected green by
      design:

  - the unchanged idem-read pin;
  - 2.6, where the helper finds no recipe rollbacks.

  **Observed (2026-09-29):** 89 failed, 18 passed, 7 skipped. The passes were the contracts
  field-list check (no database), `test_rollback_1_drops_exactly_its_objects` (vacuous: nothing to
  drop yet), 2.6, and the idem-read pin. The 7 unit checks skipped because their files did not
  exist yet. Green after 2.8: 98 passed; the regression set: 363 passed, 5 skipped.

### Implementation (green)

- [x] 2.8 Write `supabase/migrations/<T>0000_add_cyl_trait_recipe_key.sql` and its rollback R1.
  - Follow design § Migration Plan.
  - R1 includes the guard (writeback spec) and the hot-apply and `migration repair` wording.
- [x] 2.9 **Types.**
  - Hand-edit the five `database.types.ts` copies: add the recipe and run columns to
    `cyl_trait_sources`.
  - Run `make gen-types` into a scratch copy, and confirm its diff for those entries matches the
    hand edits.
- [x] 2.10 **Go green.** Run `make migrate-local`, then section 2. These must still pass:
  - `test_cyl_writeback_rpc.py`: the re-applied older bodies still insert, because the new columns
    are nullable;
  - `test_cyl_trait_source_idem_read.py`;
  - `test_cyl_pipeline_dispatch.py`;
  - `test_cyl_experiment_trait_counts.py`, whose test at :973 deletes a scan;
  - `tests/unit/test_cyl_trait_sources_grants.py`.

## 3. Write-back stamping (migration 2)

### Tests first (red)

- [x] 3.1 **Unit tests,** in the §2 unit file, written before 3.5.
  - [x] 3.1.1 `test_m2_differs_from_a9_only_in_stamping`.
    - The region is defined as in `test_cyl_writeback_a9_migration_files.py:30-31`: from
      `CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(` through the
      `bloom_workflows;` GRANT line.
    - Strip `--` comments and whitespace, then `difflib.ndiff`.
    - The removed lines and the added lines each equal an exact literal list: the INSERT columns
      and VALUES, the declared variables, the run lookup, the `UPDATE … SET scan_id`, and the
      REVOKE line.
  - [x] 3.1.2 `test_a9_is_the_newest_definition_before_m2`, following
        `test_cyl_writeback_a9_migration_files.py:100`.
  - [x] 3.1.3 `test_m2_calls_the_backfill_and_sets_owner`, and
        `test_r2_restores_a9_region_verbatim`. The ACL is checked in 3.3.2.
- [x] 3.2 **Integration tests,** in `tests/integration/test_cyl_writeback_rpc.py`.
  - Extend `_envelope` with a `provenance_extra=` keyword for realistic `predict_models`. One test
    sends none.
  - [x] 3.2.1 `test_fresh_delivery_stamps_recipe_key_and_scan_id`.
  - [x] 3.2.2 `test_dispatched_delivery_stamps_workflow_and_run`.
  - [x] 3.2.3 `test_unrequested_scan_still_stamps_run`. The run-scan row count is unchanged, and
        `status_update_matched is False`.
  - [x] 3.2.4 `test_hand_submitted_delivery_stamps_workflow_only`.
  - [x] 3.2.5 `test_ambiguous_workflow_name_stamps_no_run`: `cyl_pipeline_run_id` is NULL and
        `argo_workflow_name = W`.
  - [x] 3.2.6 `test_no_workflow_name_stamps_neither`.
  - [x] 3.2.7 `test_noop_redelivery_leaves_stamps_unchanged`. This is a negative control.
  - [x] 3.2.8 `test_failed_delivery_leaves_no_source`.
    - Parametrized over an unresolvable `image_ids`, a non-scan-grain trait, and a non-integer
      blob `file_size`.
    - Use `pytest.raises` in a savepoint, then assert that no source exists for the key.
  - [x] 3.2.9 The existing bloom#875 tests stay green. Add `test_redelivery_fallback_with_stamped_source`,
        which asserts that the stamps are unchanged by the fallback.
  - [x] 3.2.10 `test_noop_stamp_guard_detects_mutation`, in one transaction:
    1. read `pg_get_functiondef('insert_cyl_result_envelope(jsonb,text)'::regprocedure)`;
    2. inject `UPDATE cyl_trait_sources SET argo_workflow_name = p_argo_workflow_name WHERE id =
v_source_id;` after `v_was_noop := true;` (`20260928130000:135`) and `EXECUTE` it;
    3. assert that 3.2.7's check now fails.
  - [x] 3.2.11 `test_source_written_between_migrations_is_backfilled`.
    - Run `_apply_recipe_rollbacks(cur, down_to=2)`, which puts the a9 body back with M1 still
      live.
    - Deliver an envelope; its columns are NULL.
    - Apply `_sql_body(M2)`; its `recipe_key` and `scan_id` are now set.
- [x] 3.3 **Migration and rollback.**
  - [x] 3.3.1 `test_migration_2_body_is_idempotent`: overload arg counts are `[2]`, and a fresh
        delivery is stamped.
  - [x] 3.3.2 `test_rollback_2_restores_a9_body_and_grants`. After `_apply_recipe_rollbacks(cur,
down_to=2)`:
    - a fresh delivery leaves the recipe and run columns NULL;
    - the cross-Workflow fallback returns `True`;
    - `_acl_set` equals the set derived from `20260928130100`;
    - the overload arg counts are `[2]`.
  - [x] 3.3.3 `test_rollback_1_refuses_while_stamping_body_is_live`.
    - In a savepoint, with M2 live, applying only R1 raises and names `insert_cyl_result_envelope`.
    - `pytest.skip` if M2 isn't found.
- [x] 3.4 **Record the red check.** Run 3.1–3.3 against the §2 schema. Expected green by design:

  - 3.2.7, 3.2.8 and 3.2.10;
  - the existing bloom#875 tests.

  **Observed (2026-09-29):** 9 failed (the stamping tests, the between-migrations test and the
  migration-2 idempotency test). Green by design, as expected: 3.2.7, 3.2.8 (3 cases), 3.2.10 and the
  existing bloom#875 tests. Also green, vacuously, before R2 existed: 3.2.9's stamped variant (both
  sides NULL), 3.3.2 and the ACL check. Skipped until their files existed: the M2 unit checks and
  3.3.3. After 3.6: all of `test_cyl_writeback_rpc.py` passes, as do the §2 tests, the grants,
  contract-match and dispatch tests. The full `tests/unit/` run (excluding the Windows-only
  `test_weekly_backup.py`) has 63 failures, all in Windows-path/shell tests this change does not
  touch (`test_doctor.py`, `test_env_defaults.py`, the deploy-workflow tests); they are
  platform-specific to this Windows checkout.

- [x] 3.5 `test_contract_migration_match.py` still reads `pinned_version = '0.1.0a9'`, and
      `test_security_definer_grants.py` passes.

### Implementation (green)

- [x] 3.6 Write `supabase/migrations/<T>0100_stamp_cyl_trait_source_recipe_and_run.sql`.
  - It is the `20260928130000` region verbatim, plus the edits 3.1.1 allows.
  - Then `OWNER TO postgres`, `REVOKE … FROM PUBLIC, anon, authenticated`, and the four `GRANT`s.
  - Then `SELECT cyl_backfill_trait_source_recipe_identity();`.
  - Write its rollback R2.
- [x] 3.7 **Go green.** Run section 3, then all of these:
  - `test_cyl_writeback_rpc.py`;
  - `test_security_definer_grants.py`;
  - `test_contract_migration_match.py`;
  - `test_cyl_pipeline_dispatch.py`;
  - `test_cyl_trait_recipe_key.py`.

## 4. Recipe-aware reads (migration 3)

### Tests first (red)

- [x] 4.1 **Fixture and expected results,** in a new file
      `tests/integration/test_cyl_trait_recipes_read.py`.

  - Every source is seeded with a `recipe_key` and a `scan_id` consistent with its rows, except
    sources 16, 17 and 22, which are deliberately inconsistent.
  - Source ids are fixed so that `K1` is E1's default.

  **Experiment E1.**

  | Scan     | Sources and rows                                                          |
  | -------- | ------------------------------------------------------------------------- |
  | `a`      | `K1` sources 10 (traits A, B) and 50 (A only, plus one NULL-valued trait) |
  | `b`, `c` | One `K1` source each (ids 11, 12)                                         |
  | `d`      | `K2` source 30                                                            |
  | `e`      | `K1` sources 13 and 45, and `K2` source 31                                |
  | `f`      | `K1` source 14, `K2` source 32, and NULL-source rows                      |
  | `g`      | Legacy source `L` (id 5) only                                             |
  | `h`      | NULL-source rows only                                                     |
  | `i`      | No traits                                                                 |
  | `j`      | A `K3` source (id 40) with `scan_id = j` and no rows                      |
  | `j2`     | `K1` source 15, plus a `K3` source (id 41) with no rows                   |
  | `k`      | A `K1` source (id 16) with `scan_id` NULL, with rows on `k`               |
  | `m`      | Rows only from a source (id 17) whose `recipe_key` is NULL                |
  | `n`      | A `K1` source (id 22) with `scan_id = b`, whose rows sit on `n`           |

  **Other experiments.**

  - E2 shares `K1` (source 60) and has `K4` (source 70).
  - E3 has only `L` rows and NULL-source rows.
  - E4 has only NULL-source rows.

  **Expected results.**

  | Call                     | Expected                                                                                                                                                            |
  | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `list(E1)`               | `K1` is the default (`newest_source_id` 50). `n_scans`: `K1` = 7 (`a b c e f j2 k`), `K2` = 3 (`d e f`), `legacy:L` = 1, `unattributed` = 2 (`f h`). `K3` is absent |
  | `list(E1, E2)`           | `K4` is the default (70)                                                                                                                                            |
  | `list(scan_ids_ => [e])` | Exactly `K1` and `K2`                                                                                                                                               |
  | `list(scan_ids_ => [i])` | Zero rows                                                                                                                                                           |
  | `list(E3)`               | `legacy:L` is the default                                                                                                                                           |
  | `list(E4)`               | `unattributed` is the default                                                                                                                                       |

  **`coverage(E1)` against `K1`:**

  | Status         | Scans                                                         |
  | -------------- | ------------------------------------------------------------- |
  | `included`     | `a` (source 50), `b` (11), `c`, `e` (45), `f`, `j2`, `k` (16) |
  | `other_recipe` | `d`, `m` (empty `available_recipes`), `n` (empty)             |
  | `legacy_only`  | `g`, `h`                                                      |
  | `no_traits`    | `i`, `j`                                                      |

  `f`'s `available_recipes` is `[K1, K2, unattributed]` under `COLLATE "C"`.

- [x] 4.2 **`list_trait_recipes` tests.** Cover the table rows above, plus:
  - intersection (`list(E1, [a, <E2 scan>])`);
  - empty arrays for each argument;
  - both arguments NULL raises;
  - the legacy `definition` is `{source_id, source_name}`;
  - `unattributed` has a NULL `definition`, `newest_source_id` and `recipe_key_version`;
  - `definition` hashes to `recipe_key` on every pipeline row.
- [x] 4.3 **`get_trait_recipe_coverage` tests.** Cover the coverage table, plus:
  - an explicit `legacy:L` pick (the pipeline-only scans become `other_recipe`);
  - an all-`no_traits` selection returns a NULL `recipe_key`;
  - a random 64-hex key raises;
  - `K3`, which is stored, is accepted;
  - a one-scan call.
- [x] 4.4 **`get_experiment_traits` recipe-mode tests.** Cover:
  - one recipe only;
  - `e` reads from source 45;
  - `a` returns A from 50, and not B;
  - the NULL-valued trait comes back as a row;
  - `unattributed`;
  - `legacy:L` equals `source_id_ => 5` in the same order;
  - `K3` returns zero rows without raising;
  - `K4` against E1 returns zero rows;
  - these mistyped keys raise: random 64-hex, `legacy:999999999`, uppercase hex, `'foo'`;
  - each of the three selector pairs raises;
  - `scan_ids_` narrowing in all four modes, including an empty array and E2's scan ids;
  - the `recipe_key` column value in every mode.
- [x] 4.5 `test_result_columns_and_order`: `cursor.description` names equal the twelve spec
      columns in order, in every mode.
- [x] 4.6 `test_functions_agree_on_source_of_recipe`. For every `included` coverage row, every
      recipe-read row for that scan has the same `source_id`. §5 adds the dataset leg.
- [x] 4.7 **Default-path parity,** in one transaction:
  1. `_apply_recipe_rollbacks(cur, down_to=3)`, then capture `(E1)`, `(E1, source_id_)` and
     `(E1, run_id_)`;
  2. apply `_sql_body(M3)`, then capture the first eleven columns;
  3. assert both captures are equal, in the same order.
- [x] 4.8 **Grants.**
  - For each of `bloom_agent`, `bloom_user` and `bloom_admin`: `SET LOCAL ROLE`, then call all
    three functions in all five modes; each call returns rows.
  - `authenticated`: the grant is present.
  - `anon`: `has_function_privilege` is false.
  - `prosecdef = false`.
- [x] 4.9 **Gateway tests.**
  - bloommcp's exact three-key body returns HTTP 200, a JSON array, and no PGRST203.
  - `list_trait_recipes` with `{"experiment_ids_": [1]}` returns 200.
  - With the anon key, `get_experiment_traits` returns 401 or 403.
- [x] 4.10 **Migration and rollback.**
  - [x] 4.10.1 `test_migration_3_body_is_idempotent`: after re-application there is one
        five-argument overload.
  - [x] 4.10.2 `test_rollback_3_restores_three_arg_function`.
    1. After `_apply_recipe_rollbacks(cur, down_to=3)`, capture `prosecdef`, `provolatile`,
       `proconfig` and `_acl_set`.
    2. `DROP` the function, apply `_sql_body(20260728000000)`, and capture again.
    3. The two captures are equal, and neither new function exists.
    4. Re-applying M3 restores the five-argument function.
  - [x] 4.10.3 **Unit tests, written before 4.13:**
    - `test_20260728000000_is_the_newest_get_experiment_traits_before_m3`;
    - M3 has `DROP FUNCTION IF EXISTS`, `CREATE OR REPLACE`, `OWNER TO postgres` and
      `NOTIFY pgrst`, and adds no write grant or policy;
    - its presence SQL has `LATERAL … LIMIT 1` probes against `cyl_scan_traits`, including an
      `IS NULL` arm, and has no `EXISTS (SELECT … FROM cyl_scan_traits`.
  - [x] 4.10.4 `test_rollback_1_still_refuses_with_m3_live`: after R2 only, R1 raises.
- [x] 4.11 **Update the existing tests that pin the old signature.** Each keeps its original
      assertion.
  - `test_cyl_experiment_traits.py:453` and `:476`: start each with
    `_apply_recipe_rollbacks(cur, down_to=3)`. Each also asserts exactly one
    `get_experiment_traits` overload.
  - `test_cyl_experiment_traits.py:382`: change the signature string to
    `get_experiment_traits(bigint,bigint,text,text,bigint[])`.
  - `test_cyl_experiment_summary_counts.py:533` and `:548`: `pronargs` becomes 5, for
    `get_experiment_traits` only. The three-positional-argument oracle resolves unchanged.
- [x] 4.12 **Record the red check.** Run 4.2–4.11 against the §3 schema. Expected green by design:

  - 4.10.4;
  - the oracle usage in the summary-counts tests.

  **Observed (2026-09-29):** 44 failed: every new recipe-read test, plus the edited existing tests
  that now expect the five-argument form. The unedited existing read tests (default path, source
  and run pins, cross-experiment isolation, summary-count oracles) stayed green, as expected. After
  4.13: 407 passed, 7 skipped (gateway tests, which run only in CI). Two test bugs were fixed on the
  way: a missing savepoint after an expected raise, and re-creating the old body as
  `supabase_admin` instead of `postgres` (owner and default grants differ).

### Implementation (green)

- [x] 4.13 Write `supabase/migrations/<T>0200_add_cyl_trait_recipe_reads.sql` (design D5, D6).
  - Write its rollback R3, whose header says R4 must run first.
  - Add a header note to `supabase/rollbacks/20260728000000_get_experiment_traits_rollback.sql`
    saying R3 must be applied first.
  - Edit the three function entries in the types files, with the scoped `gen-types` diff check.
- [x] 4.14 **Go green.** Run section 4, then:
  - `test_cyl_experiment_traits.py`;
  - `test_cyl_experiment_summary_counts.py`;
  - `test_cyl_read_path.py`;
  - `test_cyl_pipeline_dispatch.py`;
  - `test_cyl_trait_recipe_key.py`.

## 5. Datasets (migration 4)

- [x] 5.1 **Characterization, first and green.** New file
      `tests/integration/test_cyl_dataset_recipe_mode.py`, with `test_source_mode_characterization`.
      Run it against the §4 schema. It pins:

  - timepoints filter scans;
  - QC exclusion;
  - an unknown QC name applies no filter;
  - one source's rows only;
  - `_acl_set` equals `{PUBLIC, anon, authenticated, service_role, postgres}` with `EXECUTE`.

  Commit it on its own.

### Tests first (red)

- [x] 5.2 **Tests in the same file.**
  - [x] 5.2.1 `test_frozen_rows_do_not_change`.
  - [x] 5.2.2 `test_source_mode_records_recipe`.
  - [x] 5.2.3 **Recipe mode:**
    - spans per-scan sources;
    - reads the highest source within the recipe;
    - leaves out other recipes;
    - `unattributed`;
    - `legacy:S` equals source mode;
    - timepoints and QC apply;
    - matches the recipe read for scans with an accession and a species.
  - [x] 5.2.4 Add the dataset leg to 4.6.
  - [x] 5.2.5 `test_selector_errors`: zero selectors, two selectors, or an unknown recipe each
        raise, and nothing is written. Also cover the behavior change: a NULL `trait_source_id` alone
        now raises.
  - [x] 5.2.6 `test_dataset_function_properties`:
    - one overload, **with six arguments**;
    - `prosecdef = false`;
    - owner `postgres`;
    - `proconfig` contains `statement_timeout=0`;
    - `_acl_set` equals 5.1's set.
  - [x] 5.2.7 **Gateway tests.**
    - bloomctl's five-key body with `trait_source_id: null` returns HTTP 400 with the "exactly one
      selector" P0001 message, not PGRST202 or PGRST203.
    - A six-key body with an unknown `recipe_key` returns 400 with the unknown-recipe message.
    - Names are `uuid4`. In `finally`, an autocommit connection deletes any `cyl_dataset_traits`
      and `cyl_datasets` rows with that name.
  - [x] 5.2.8 `test_datasets_recipe_key_backfill_and_check`, in one transaction:
    1. `_apply_recipe_rollbacks(cur, down_to=4)`;
    2. insert three datasets directly (pipeline `S`, legacy `L`, and NULL);
    3. apply `_sql_body(M4)` and assert the three keys;
    4. apply it again: nothing changes;
    5. the CHECK accepts and rejects correctly.
  - [x] 5.2.9 `test_migration_4_body_is_idempotent` and `test_rollback_4_restores_five_arg`.
    - The rollback test asserts the ACL set, the `20240904033106` body, that the column is
      dropped, and the recipe-mode NOTICE count.
  - [x] 5.2.10 **Unit tests:**
    - `test_20240904033106_is_the_newest_create_cyl_dataset_before_m4`;
    - M4 has `lock_timeout`, a named and guarded `cyl_datasets_recipe_key_format_check`,
      `DROP … IF EXISTS`, `CREATE OR REPLACE`, `OWNER TO postgres`, `NOTIFY pgrst` and
      `SET statement_timeout TO '0'`;
    - neither M4 nor R4 contains `alter database` or `alter role`;
    - R4's function region equals `20240904033106`'s.
- [x] 5.3 **Record the red check** against the §4 schema. Expected green by design: 5.1 and 5.2.1.

  **Observed (2026-09-29):** 13 failed; 5.1 and 5.2.1 green as expected; the two gateway tests
  skipped. After 5.4: 15 passed, 2 skipped (gateway, CI only); one test bug fixed (the assertion
  called `_frozen` before fetching the recipe read on the same cursor). `bloomcli`'s mocked
  `tests/test_cyl_datasets.py`: 36 passed, 2 failed in rich-table width rendering on this Windows
  terminal; the branch has no `bloomcli/` change. The spec now states that recipe mode sees only
  scans in the recipe-read scan set (plants with an accession).

### Implementation (green)

- [x] 5.4 Write `supabase/migrations/<T>0300_add_cyl_dataset_recipe_mode.sql` and its rollback R4.
      Edit the `cyl_datasets` and `create_cyl_dataset` entries in the types files, with the scoped
      `gen-types` check.
- [x] 5.5 **Go green.** Run section 5, `test_cyl_pipeline_dispatch.py`, and
      `bloomcli/tests/test_cyl_datasets.py`, which is mocked and unchanged.
- [x] 5.6 **`test_full_rollback_chain_round_trip`,** in one transaction:
  1. `_apply_recipe_rollbacks(cur, down_to=1)`;
  2. an a9 delivery works, and so do the three-argument read and the five-argument dataset
     function;
  3. none of this change's objects remain;
  4. re-apply M1–M4 and assert the new state.

## 6. Docs and sidecar

- [x] 6.1 **Types.** The four generated `database.types.ts` copies are byte-identical (compare
      with `sha256sum`), and the hand-maintained `web/types/database.types.ts` carries the same
      entries.
- [x] 6.2 **Tests first,** in unit file `tests/unit/test_trait_recipe_export_schema.py`:

  - the schema parses;
  - the example has every `required` property at every level;
  - every schema property has a row in the field table in `trait-recipes.md`;
  - every source that row names is one of:
    - a column in M3's `RETURNS TABLE` lists;
    - a key path under `cyl_trait_sources.metadata`;
    - `exporter-supplied`.

  Record it red.

- [x] 6.3 **Write the sidecar files and the page,** under `_WIKI/SUPABASE/`:
      `trait-recipes.export.schema.json`, `trait-recipes.export.example.json` and `trait-recipes.md`.
  - The page links to the spec requirements instead of restating them.
  - It covers:
    - latest versus default recipe;
    - recipe versus idempotency key versus source, with a pointer to
      `list_experiment_trait_sources`;
    - the pseudo-recipes;
    - that `legacy_only` includes unattributed-only scans;
    - one `get_experiment_traits` call per experiment in a multi-experiment export;
    - the blind spot (contracts#45);
    - the field table.
  - The recipe-mode dataset call shape (`trait_source_id: null`) goes in the §5 commit, so that
    datasets can be split off cleanly if review stalls.
  - 6.2 goes green.
- [x] 6.4 **Update the existing docs.** Keep the phrases that `tests/unit/test_refresh_workflow_staleness_docs.py`,
      `test_bloommcp_local_mode_docs.py` and `test_bloommcp_data_mount_rename.py` require.
  - `_WIKI/BLOOMMCP/README.md:147-185`:
    - the five-argument signature;
    - one source per scan for pipeline data;
    - prefer `list_trait_recipes` and `recipe_key_` over a single-source pin;
    - link the page.
  - `_WIKI/SUPABASE/README.md`:
    - § Write-back RPC: the recipe and run columns; a no-op never rewrites them; the run stamps are
      never backfilled;
    - § Pipeline-trigger tables: the new index, the `cyl_pipeline_run_id` FK, and that
      `bloom_workflows`' column grant excludes the new columns;
    - link the page.
  - `_WIKI/README.md` layout tree (:16-23): add the three new files.
  - `contracts/README.md`:
    - the a3 note at :86-92: `predict_output_params` is now keyed by `recipe_key` v1 (link the
      page);
    - § Re-pin procedure: add a step 7. If a keyed Provenance field changes, decide whether
      `recipe_key_version = 2` is needed.
  - `services/workflows/README.md:182`: this `pipeline_run_id` is the value
    `cyl_trait_sources.cyl_pipeline_run_id` stores.
  - `bloomcli/README.md:83-84`: a pipeline source name selects one scan's rows; per-recipe
    datasets come with #481. This goes in the §5 commit.
- [x] 6.5 Run `make erd` and commit `_WIKI/SUPABASE/erd.md`. On a rebase, regenerate it; never
      hand-merge it.

## 7. Pre-merge

- [x] 7.1 **Run `/pre-merge`, plus:**
  - `uvx ruff@0.9.9 check` and `format --check` on the new Python files only. Existing test files
    carry unrelated violations, so check only the lines this change edits there.
  - `uv run pre-commit run --files <changed files>`, then confirm `--check` in 2.1 still passes.
    Prettier may reformat the vectors JSON; tests compare parsed values.
  - The full `tests/unit/` and `tests/integration/` suites, with the stack up.
  - `bloomcli/tests/test_cyl_datasets.py`.
  - `scripts/lint_migrations.sh origin/staging` and `scripts/lint_migration_isolation.py`.
  - Both `openspec validate` runs.
  - A manual check that no other active change MODIFIES this change's requirements.

  **Observed (2026-09-29):**
  - Typecheck as CI runs it: `packages/bloom-js`, `packages/bloom-fs` and `web` (`tsc --noEmit`)
    all pass.
  - Full `tests/integration/` on the dev stack: 1,641 passed, 17 skipped, 74 failed and
    121 errors. None of the failures are in files this change touches:
    - the gateway, Caddy, smoke and PostgREST tests need the full stack on port 80;
    - the `lint_*` and `promote_security` tests need bash tooling;
    - `test_phenotyping_supabase_tools.py` soft-deletes via a column the view does not filter;
    - `test_bloommcp_usage_rpc.py` counts committed rows left by earlier runs.
  - Every cyl, recipe and read test passed.
  - `pre-commit` (prettier) reformatted 24 files. Kept only on files this PR creates. Reverted on
    the generated `database.types.ts` copies (about 1,900 reflowed lines each; CI does not run
    prettier, and they are `supabase gen types` output) and on pre-existing READMEs and specs.
  - pip-audit and Docker builds were skipped: no lockfile, dependency or Dockerfile changed.
  - `bash scripts/lint_migrations.sh origin/staging` and `lint_migration_isolation.py` pass.
  - Both `openspec validate` runs pass. No other active change MODIFIES this change's
    requirements (D11).
- [x] 7.2 **Staging dry run, read-only.** Done 2026-09-29 with
      `tests/integration/fixtures/recipe_backfill_dry_run.sql` (drift-pinned to M1 by unit tests):
      85 sources, 85 keyed, 10 distinct pipeline keys, 80 of 80 resolved, 79 of 79 agreeing, no
      errors, 4.6 s. The same query on the dev DB matched what migration 1 actually wrote there.
  - Extract the backfill function's body from M1, and run its SELECTs as CTEs with the helper
    inlined. Diff the extracted SQL against M1 first, so it cannot drift.
  - Commit the query as `tests/integration/fixtures/recipe_backfill_dry_run.sql`, which 8.0 reuses.
  - Record: 85 of 85 keys, 80 of 80 `scan_id`s, 10 distinct pipeline keys, 79 of 79 agreeing with
    trait rows, and no errors.
- [x] 7.3 **Coverage timing, read-only on staging (manual, not CI).** Done 2026-09-29: the
      presence probes for experiment 1 (18,471 scans x 5 legacy sources, plus the NULL-source and
      has-traits probes) ran in 711 ms, every probe an index-only scan and no sequential scan of
      `cyl_scan_traits`. Run the M3 probe SQL for
      experiment 1 under `EXPLAIN ANALYZE`. It must be well under 8 s; it took 450 ms on 2026-09-29.
- [x] 7.4 **Timestamps.** Done 2026-09-29: staging gained `20260930001749`, so the branch was
      rebased onto `6733eca1` and the eight files restamped `20260929230x00` → `20260930050x00`. The dev
      DB's four old history rows were removed (what `migration repair --status reverted` does; the CLI
      refused TLS), and `make migrate-local` re-applied all four on top of their existing objects. Restamped again on 2026-09-30 to
  `20260930120x00` after staging gained `20260930060512` (rebased onto `4ee7dc11`).
  - Fetch `origin/staging`.
  - If a newer migration has landed, `git mv` the four files and their rollbacks, keeping their
    order.
  - Repair the dev database with `supabase migration repair --status reverted <old>`, then re-run
    the suite.
- [x] 7.5 **PR body.** Drafted 2026-09-29; `make pr-body-check` passes and it has no closing
  keywords.
  - Write it with `/pr-description`.
  - Its **Schema changes** section comes from `make erd-snapshot CHANGED=origin/staging`. It lists
    every named constraint and index from both migrations.
  - It says "Part of #935, #937", with no closing keywords anywhere, including commit messages.
  - It gives the per-commit review order:
    - §2–§3 for Benfica (@blm3886);
    - §4 for egao28;
    - §5 for Benfica, as the bloomctl datasets path;
    - §6 for eberrigan.
  - It notes the `erd.md` and timestamp overlap with Benfica's video-queue branch.
  - It notes the pre-deploy lock check (design § Risks).
  - Check it with `make pr-body-check BODY=<file>`.
- [ ] 7.6 **Open the PR to `staging` only after eberrigan says yes.**
  - It is squash-merged, and eberrigan merges it.
  - If the push of a new branch returns 500, create the ref through the REST API first.
  - Confirm `compose-health-check` is green; the gateway tests run only in CI.

## 8. After merge

- [ ] 8.0 **Before promoting staging to main: eberrigan runs a read-only dry run on prod.** Use
      the 7.2 query, which is committed at `tests/integration/fixtures/recipe_backfill_dry_run.sql`.
      Record the counts here. Run `scripts/lint_migrations.sh origin/main` on the promotion PR.
- [ ] 8.1 **Read-only checks on staging after deploy:**

  - `count(*) WHERE recipe_key IS NULL` is 0;
  - the NULL `scan_id`s are only the known unresolvable sources;
  - 10 distinct pipeline keys;
  - `list_trait_recipes(ARRAY[12880747])`, the A4 pipeline E2E experiment, defaults to the a9
    recipe;
  - experiment 1's coverage completes in under 8 s through PostgREST.

  If any `recipe_key` is NULL, run `SELECT cyl_backfill_trait_source_recipe_identity();` as
  `postgres`, with eberrigan's yes.

- [ ] 8.2 On the first Bloom-dispatched run after deploy, confirm the new source's stamps. If no
      such run has happened, record this as blocked.
- [ ] 8.3 **Drafts for eberrigan to approve before posting:**
  - a note on #936 for egao28, covering:
    - the new arguments;
    - "one recipe per frame";
    - `refactor-supabase-reader-db-tier2`'s "One source per frame";
    - `bloommcp/docs/data-access-roadmap.md:276` and `bloommcp/docs/storage-backends.md:62-70`;
  - notes on #865, #481 and #482;
  - the recipe-retirement follow-up issue (design D10).
- [ ] 8.4 After 8.1, close #935 and #937, with eberrigan's yes.
- [ ] 8.5 After the staging→main promotion is verified, run
      `/openspec:archive add-cyl-trait-recipe-key`. Archive `fix-cyl-redelivery-blob-collision` before
      or with it (design D11).
