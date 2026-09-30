# Design: recipe key, recipe-aware reads, and run stamping

The rules are in the delta specs. This file records why, plus the implementation constraints the
specs do not state.

## Terms

| Term                    | Meaning                                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| a7 / a9                 | sleap-roots-contracts `v0.1.0a7` / `v0.1.0a9`                                                                                                      |
| srp#N                   | talmolab/sleap-roots-pipeline#N                                                                                                                    |
| Hand-submitted run      | An `argo submit` outside Bloom's dispatcher. It has no `cyl_pipeline_run_scans` rows                                                               |
| Idempotency gate        | The write-back RPC's step 5: `INSERT … ON CONFLICT (idempotency_key) DO NOTHING` into `cyl_trait_sources`                                          |
| Latest                  | `get_experiment_traits`' unpinned per-scan `max(source_id)`. This change does not alter it, and it can mix recipes                                 |
| Default recipe          | The one recipe `list_trait_recipes` proposes for a whole selection (D5)                                                                            |
| Newest / highest source | Highest `source_id`. Sources have no timestamp, and identity ids increase                                                                          |
| Stored recipe           | A `recipe_key` present on some `cyl_trait_sources` row. `unattributed` is never stored there; a recipe-mode dataset may store it on `cyl_datasets` |
| Sidecar                 | The `<stem>.export.json` file that describes an export (D8)                                                                                        |
| Exporter                | Whatever writes an export: the web download, bloomctl or bloommcp. The pipeline is called the _producer_                                           |
| PGRST202 / PGRST203     | PostgREST errors: no matching function / ambiguous overload                                                                                        |

## Context

**What this was checked against, on 2026-09-29:**

- Bloom `origin/staging` at `21487acc`, which includes PR #940.
- The staging database, read-only.
- sleap-roots-contracts `v0.1.0a9-2-gbaead42`. Its `identity.py`, `hashing.py` and `models.py`
  are unchanged from the tag.

**Staging, as of 2026-09-29.**

| Item                            | State                                                                                                                                                                                           |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cyl_trait_sources`             | 85 rows: 5 legacy (ids 1–5, `metadata` NULL), 65 a7 pipeline (6–203), 15 a9 pipeline (219–233)                                                                                                  |
| Scan resolution                 | Every pipeline source's `inputs.image_ids` resolves to exactly one scan. For 79 of them, that is the scan their trait rows carry. Id 70 is a `deadbeef` verification fixture with no trait rows |
| Recipes                         | 10 distinct v1 keys over the 80 pipeline sources: 9 real recipes plus the fixture. The 15 a9 sources share one                                                                                  |
| Run stamps                      | `metadata->>'pipeline_run_id'`, `argo_workflow_uid` and `produced_at` are null on all 80                                                                                                        |
| Plants and experiments          | 0 of 40,203 plants lack an accession; 0 experiments lack a species                                                                                                                              |
| Postgres                        | 15.14                                                                                                                                                                                           |
| `get_experiment_traits` EXECUTE | The four read roles, plus `anon` and `service_role`                                                                                                                                             |
| `create_cyl_dataset` EXECUTE    | `PUBLIC`, `anon`, `authenticated` and `service_role`; owned by `postgres`                                                                                                                       |

**The code this change builds on.**

- **`insert_cyl_result_envelope(jsonb, text)`:**
  - Its body is at `20260928130000_cyl_writeback_contract_a9.sql:44`, and its ACL is set by
    `20260928130100`.
  - It inserts at the idempotency gate (:127-130) before step 6 resolves the scan (:203-228).
  - It stores the Provenance object as `metadata`.
  - It uses `p_argo_workflow_name` only in the run-scan status updates.
- **`get_experiment_traits(bigint, bigint, text)`:**
  - It is at `20260728000000:31-100`.
  - Its only non-test caller is bloommcp's `SupabaseReader`, which passes a named dict
    (`supabase_reader.py:145-152`) and reads columns by name.
- **`create_cyl_dataset(text, bigint, bigint, json, json)`:**
  - It is at `20240904033106:1-48`.
  - bloomctl calls it with five named keys (`bloomcli/src/bloomctl/cyl/datasets.py:303-317`).

## Decisions

### D1. recipe_key v1: the contracts idempotency payload without its per-scan inputs

**The shape.** The payload (writeback spec, "Recipe key v1 definition") follows contracts
`identity.py:8-61`, minus `scan_key`, `images_checksum` and `param_hash`. Two sources share a v1
key exactly when they would have shared an idempotency key on the same scan with the same params.

**Why `param_hash` is excluded:** it hashes `{species, mode, age}`, so it varies with plant age.

**Recorded but not keyed:**

| Field                                            | Why it is not in the key                                           |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| `contract_version`                               | The a7→a9 step was a schema-`$id` restamp, and identity ignores it |
| `sleap_nn_version`, `traits_sleap_roots_version` | Implied by `predict_code_sha` and `traits_code_sha` respectively   |
| Container digests                                | Empty on some a7 rows. A same-code rebuild must not split a recipe |
| `predict_inference_config`                       | `device` and `batch_size` are excluded from identity by design     |
| Run and orchestration fields                     | They vary per delivery                                             |

**Age-window model switches** make a different recipe (eberrigan, 2026-09-28). Models come from
a moving W&B `production` alias, so only the resolved models prove "same models".

**Two helpers.** The payload helper is what `list_trait_recipes` returns as `definition`, so a
definition always hashes to its key.

- **They never raise.** The RPC does not validate these fields, so a raise would reject envelopes
  that the `20260928130000` body accepts. In the backfill, a raise would stall the deploy, as the
  a7→a9 cutover guard did (bloom#685).
- **`IMMUTABLE` is safe.** They are declared `IMMUTABLE` although they call `STABLE` built-ins
  (`convert_to`, `jsonb_agg`). That matters only for indexes or generated columns, and neither
  uses them.
- **`COLLATE "C"`** keeps the triple sort locale-independent. The precedent is `20260911082453:388`.
- **Built-in `sha256()`.** pgcrypto's `digest()` would not resolve under the RPC's
  `search_path = pg_catalog, public, pg_temp`.
- **Grants.** The read RPCs are `SECURITY INVOKER` and call the payload helper, so the read roles
  need `EXECUTE`. The functions are pure.

**Not byte-equal to Python.** `jsonb::text` orders keys by length first, and it keeps numeric
scale, so the key is not byte-equal to contracts' `canonical_json`. Only Bloom compares recipe
keys (contracts#47 asks whether contracts should own one).

- A golden-vector partition test checks the property that matters instead: same payload, same key.
- The one documented divergence is `1` versus `1.0` in `predict_output_params`. Contracts'
  `hashing._normalize` collapses integer-valued floats; jsonb keeps them distinct. Predict emits
  `{"peak_threshold": 0.2}` as of 2026-09-29.
- jsonb also keeps a trailing zero (`0.20` and `0.2` hash differently). Pydantic never writes
  `0.20`, so only a hand-built envelope can hit this.
- **A key value is frozen.** `recipe_key_v1_expected_keys.json` holds each golden vector's key, and
  a test compares the helper's output to it. One vector's registry ids sort differently under
  `COLLATE "C"` and `en_US`, so a dropped collation fails it (checked 2026-09-30 by removing it in
  a rolled-back transaction).

**Versioning.** A redefinition is `recipe_key_version = 2`, with new helpers. v1 values are never
rewritten.

**Known blind spot.** The traits step's sleap-roots Pipeline class, chosen by species, mode and age
window, is not in provenance. Neither key sees it except through `traits_code_sha` and age.
contracts#45 (`traits_pipeline_class`) would let a v2 key include it.

**Provenance without the keyed fields** (eberrigan, 2026-09-30). Object provenance with no models
and no code shas hashes to the empty payload's key, so all such sources share one `pipeline`
recipe. It is documented, not special-cased: contracts requires the fields, so no bloomctl
delivery can produce it, and keying these as `legacy:<id>` would add a fourth edit to the a9 RPC
body. The 8.0 dry run counts them as `empty_payload`, expected 0. The dev DB's 94 test-residue
sources are all of this kind.

### D2. scan_id is set after scan resolution, in the same transaction

The source is inserted at the idempotency gate, before step 6 resolves the scan. The gate cannot
move later: on a no-op re-delivery, the scan must come from the existing source's run-scan row
(the bloom#875 fallback), not from this delivery's `image_ids`.

So the fresh-insert path runs `UPDATE cyl_trait_sources SET scan_id = v_scan_id WHERE id =
v_source_id` right after step 6 (:228). The other four columns go into the `INSERT`.

### D3. Run and Workflow stamping reads the dispatcher's record

**Why a Workflow name can match more than one run.** Argo deletes a Workflow one hour after it
finishes (`WORKFLOWS_K8S_TTL_SECONDS`), so a generated name suffix can recur. When more than one
run matches, NULL is safer than a guess.

**The index.** `cyl_pipeline_run_scans(argo_workflow_name)` serves the lookup.

**The column name.** `cyl_pipeline_run_id` was eberrigan's choice (2026-09-29).
`pipeline_run_id` already names two other things:

- the producer's text id, in `metadata`, in the `cyl_scan_traits_source` column, and in the
  `run_id_` pin;
- the workflows API response field (`services/workflows/README.md:182`). That field's integer is
  the value `cyl_pipeline_run_id` stores, and that README gets a note.

**No backfill.** Nothing recorded which Workflow wrote an existing source. The bloom#875 fallback
also stamps `source_id` onto a re-delivering Workflow's run-scan row, so those rows do not prove
authorship.

**The run stamp is best-effort** (eberrigan, 2026-09-30). `cyl_pipeline_run_id` records the run
the dispatcher associated with the Workflow name the writer supplied. It is not proof that the run
produced the values.

- **A recycled name can match a stale run.** The lookup reads all run-scan history, so a Workflow
  that reuses the name of exactly one earlier run is stamped with that run. That needs a
  `generateName` suffix collision after Argo's TTL has deleted the first Workflow. The a9 body's
  run-scan status update already matches the name the same way (`20260928130000:159,188`).
- **A Workflow whose name was never recorded gets no run.** Only `complete_cyl_pipeline_batch`
  writes a name to run-scan rows (`20260817120000:195`). If the worker dies after submitting W1
  and the retried message submits W2, W1's deliveries keep `argo_workflow_name = W1` and a NULL
  run, and no later repair can derive one.

### D4. Backfill

**One function, called twice.** `cyl_backfill_trait_source_recipe_identity()` is created and
called by migration 1, and called again at the end of migration 2. `db push` commits each file
separately. A write-back that commits between migration 1's commit and migration 2's
`CREATE OR REPLACE` runs the `20260928130000` body and leaves NULL columns, and the second call
fills them in.

**It does not close every gap.** A write-back already running the old body when migration 2
commits, and committing after migration 2's backfill statement, keeps NULL columns, because the
backfill cannot see its uncommitted row. The remedy is to re-run the function as `postgres`, which
is safe to repeat. Task 8.1 therefore checks `count(*) WHERE recipe_key IS NULL = 0`, not a fixed
total.

**Scan resolution is guarded.** A `~ '^[0-9]{1,18}$'` test comes before any `::bigint` cast, so
the cast cannot raise. A non-array `image_ids`, or a JSON `null` element, counts as unresolvable;
the RPC raises on both.

**No read of the traits table.** It never reads `cyl_scan_traits` (28.9M rows, and no index leads
with `source_id`). Agreement with the trait rows is checked in tests and in the pre-merge staging
dry run.

### D5. How recipe presence is found

The rule is in the read spec, "Recipe presence is defined by trait rows". This section records
how the functions implement it.

**The implementation constraint.** For each selected scan `s`:

- **The candidates** are the sources with `scan_id = s` (via the new index), plus every source
  with `scan_id IS NULL`: the legacy sources, and any pipeline source the backfill could not
  resolve.
- **Each candidate `c` is confirmed** by `LATERAL (SELECT 1 FROM cyl_scan_traits WHERE scan_id = s
AND source_id = c LIMIT 1)`.
- **The NULL source is confirmed** by a separate `… AND source_id IS NULL LIMIT 1` probe. The
  probe cannot use `IS NOT DISTINCT FROM`, which a btree index cannot serve.
- **The index.** Both probes use the `scan_source_trait_uniqueness` `(scan_id, source_id,
trait_id)` index.

**Measured on staging (2026-09-29, read-only)** for experiment 1: 18,471 scans and 5 legacy
sources.

| Form                                                       | Time                                 |
| ---------------------------------------------------------- | ------------------------------------ |
| `LATERAL … LIMIT 1`                                        | 450 ms                               |
| `EXISTS` (planned as a hash aggregate over all 28.9M rows) | 7.0 s, against PostgREST's 8 s limit |

A unit test pins the `LATERAL … LIMIT 1` text, because `EXPLAIN` cannot see inside plpgsql.

**No selection, no scans.** `_cyl_trait_recipe_presence` must be executable by the read roles,
because the `SECURITY INVOKER` wrappers call it, and that makes it reachable over PostgREST. With
both selectors NULL it would probe every scan in the database, bounded only by
`statement_timeout`, so it selects no scans then. The public wrappers raise on that call instead.

**Why "default recipe" means the most recent recipe.** It is the most recently written recipe,
not the one covering the most scans. `n_scans` lets a caller choose by coverage instead.

**Why the functions take the arguments they do.**

- The scan universe is `get_experiment_traits`' join chain, so coverage and reads agree on which
  scans exist.
- `list_trait_recipes` and coverage take arrays, so that one selection can span experiments.
- `get_experiment_traits` stays per-experiment, because every caller reads one experiment. A
  multi-experiment export makes one call per experiment with the same `recipe_key_`.
- `list_experiment_trait_sources` is unchanged. It lists sources, which for pipeline data means
  one per scan.

### D6. Replacing two functions in place, not expand/contract

`.claude/commands/database-migration.md:130-136` asks for expand/contract on RPC signature
changes. Migrations 3 and 4 replace `get_experiment_traits` and `create_cyl_dataset` in place
instead:

- **The forms cannot coexist.** The new arguments have defaults, so the old and new overloads
  would both match a named call such as bloommcp's `{experiment_id_, source_id_, run_id_}`. That
  is PGRST203, and it is why `20260701000000` dropped the 2-argument `get_scan_traits`.
- **No caller needs to move.** Callers pass named arguments and read columns by name: bloommcp,
  and bloomctl's dataset call. No TypeScript references either function outside the generated
  types.
- **It is atomic.** Each file drops and recreates in one transaction and ends with `NOTIFY pgrst`.
  Until PostgREST reloads, its cache briefly holds the old signature. Named calls still resolve in
  Postgres during that window, and the extra trailing column is harmless.

A new function name was the alternative. It would leave two read paths to keep in parity.

**Owner.** Every function this change creates or recreates is followed by
`ALTER FUNCTION … OWNER TO postgres`. So its owner and default grants match `db push` even when a
test or a hand-applied rollback runs it as `supabase_admin`. The resulting ACLs are in the specs.

### D7. Datasets

- **Where it lives.** `cyl_datasets.recipe_key`, its CHECK and its backfill are in migration 4,
  with the function that uses them.
- **No default recipe.** A dataset is a reproducibility artifact, so the caller names the recipe.
- **The call shape.** A PostgREST recipe-mode call still sends `trait_source_id: null`,
  `qc_set_name` and `timepoints`, because only the new trailing argument has a default.
- **The argument name.** It is `recipe_key`, following this function's own naming. The read
  functions use `recipe_key_`.
- **Name collision.** The plpgsql argument `recipe_key` collides with the `recipe_key` columns, so
  the body aliases it (`_recipe_key := recipe_key`), as the existing body does for its other
  arguments.
- **What is recorded.** Excluded scans are not stored; the frozen rows are the record.
- **The scan set.** The dataset's scan set keeps the `cyl_scans_extended` filter from
  `20240904033106`:
  - it inner-joins `species` and not `accessions`;
  - so it can differ from `get_experiment_traits`' set in both directions;
  - neither case occurs on staging (2026-09-29).
- **Telling the modes apart.** A source-mode dataset stores its source's `recipe_key` too, so
  `trait_source_id IS NULL` marks recipe mode.
- **Recorded, not enforced** (eberrigan, 2026-09-30). "Exactly one selector" is checked only
  inside `create_cyl_dataset`, the one writer in Bloom's code. The table's policies still let
  `authenticated` INSERT, `bloom_writer` INSERT and UPDATE, and `bloom_admin` do anything, so a
  direct write can set a `recipe_key` that does not match the frozen rows. `trait_source_id` has
  had the same looseness since before this change. No trigger or CHECK was added: a trigger would
  also block `bloom_admin` corrections and change `trait_source_id`'s existing updatability.

### D8. Export sidecar v1

**What ships in this PR,** under `_WIKI/SUPABASE/`, which `lint_migration_isolation` allows:

- `trait-recipes.md`, the reader-facing page;
- `trait-recipes.export.schema.json`, a JSON Schema (draft 2020-12);
- `trait-recipes.export.example.json`.

**The export's files.** An export writes three files:

- `<stem>.csv`: one header row and no `#` lines, with `recipe_key` and `source_id` columns;
- `<stem>.export.json`: the sidecar, which holds the authoritative excluded list;
- `<stem>.excluded.csv`: an optional flat copy of that list.

**How it is checked here.** The root test environment has no `jsonschema` package, and adding it
needs a root `pyproject.toml` change, which is outside the migration surface. A unit test checks:

- that the schema parses;
- that the example carries every `required` property;
- that every property is mapped in the page's field table.

Full validation against the schema lands with the first exporter, #865 or #481.

### D9. No unrequested run-scan rows

#937 step 3, a `requested = false` row per unrequested scan, was dropped (eberrigan, 2026-09-29).

- **Its cause is going away.** The cause is srp#71's `run_manifest.json` union. PR #940
  (`1bc3056c`, merged 2026-09-29) removes it once the cluster templates pin a bloomctl image that
  includes it. On that date, Bloom's live-template fixtures still pinned `bloomctl:sha-28034f6`.
- **It needs code outside this PR.** `services/workflows/status_poller.py` would also have to
  change, because `done_count` counts every `written` or `reused` row (:212, :272). That file is
  outside the migration surface.
- **D3 still covers it.** D3 records the run on such a scan's source.
- **Until the re-pin,** bloomctl prints a misleading non-retriable "failed" line for such a scan.

### D10. No recipe retirement

After a rollback, the bad build's sources keep the highest `source_id`, so the default recipe
keeps choosing it (`openspec/changes/archive/2026-09-28-repin-cyl-contract-a9/design.md`
§ Rollback). A `retired_at` flag would fix that.

It was deferred (eberrigan, 2026-09-29): it has never been needed, and adding it later means one
table and a change to the default rule. The workaround is an explicit `recipe_key_`.

### D11. Spec deltas and archive hazards

- **cyl-trait-writeback: ADDED only.** The stale, unarchived `fix-cyl-pipeline-run-scan-status`
  MODIFIES "Write-back RPC ingests a ResultEnvelope" with text that predates the bloom#875
  fallback (`archive/2026-09-18-fix-cyl-redelivery-status-fallback/design.md:204-214`). So this
  change states the behavior it preserves inline.
- **The `bloom_workflows` column list** (`id, metadata, idempotency_key`) relies on
  `fix-cyl-redelivery-blob-collision`, which is unarchived and ADDs the `idempotency_key` grant.
  That change should be archived before or with this one.
- **cyl-trait-read.**
  - It MODIFIES "Bulk experiment-scoped trait reads" and "Additive, non-destructive bulk-read
    migration". The second's rollback scenario would otherwise be false.
  - It leaves "Bulk trait read functions are callable by the read roles" as it is. Its "without any
    new grant" still holds, because this change re-grants the same four roles and adds only the
    `anon` revoke.
  - `fix-cyl-scan-traits-latest-rollup` names `get_experiment_traits(experiment_id_, source_id_,
run_id_)`, which is still a valid named call.
  - No active change touches the requirements this change modifies.
- **bloommcp.** The unarchived `refactor-supabase-reader-db-tier2` spec describes pinned
  `get_experiment_traits` calls ("One source per frame"). It stays valid, and the #936 note (task
  8.3) points egao28 at it.
- **Strict validation.** `--strict` reads only a requirement's first physical line for SHALL/MUST.

## Risks / Trade-offs

**Locks.** Migration 1 takes three locks:

- ACCESS EXCLUSIVE on `cyl_trait_sources`, which every trait read joins;
- SHARE ROW EXCLUSIVE on `cyl_scans` and `cyl_pipeline_runs`, for the foreign keys;
- SHARE on `cyl_pipeline_run_scans`, for the index, which blocks the poller's writes.

Migration 4 takes ACCESS EXCLUSIVE on `cyl_datasets`.

Both set `SET LOCAL lock_timeout = '5s'` (precedent: `20260924120000:52`). A bloommcp read of
experiment 1 (about 7 s), a batch write-back, or a running `create_cyl_dataset` (whose
`statement_timeout` is 0) can therefore fail the deploy. That failure is safe; the remedy is to
re-dispatch. Before deploying, check that no pipeline batch is running and that no
`create_cyl_dataset` appears in `pg_stat_activity`.

**Partial deploys are valid.** Any prefix of migrations 1–4 is a consistent state: stamping works,
and reads not yet replaced keep their old form. The next deploy applies the rest.

**Prod data has not been measured.** Task 8.0 is eberrigan's read-only prod dry run before the
staging-to-main promotion.

**Legacy source 4 (`test`)** becomes the default recipe `legacy:4` on experiment 7206207, with
values identical to source 2. The data fix is a separate decision for eberrigan and Benfica
(@blm3886).

## Migration Plan

Four files, each with a `supabase/rollbacks/` partner. `<T>` must be greater than staging's newest
migration at push time (`20260929204846` on 2026-09-29). It is re-checked with
`scripts/lint_migrations.sh` right before the merge. Tests locate files by glob.

| #   | File                                                | Contents                                                                                                                                                                                               |
| --- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `<T>0000_add_cyl_trait_recipe_key.sql`              | Helpers, backfill function, the recipe and run columns, named FKs and CHECKs, three indexes, backfill call, `NOTIFY pgrst`                                                                             |
| 2   | `<T>0100_stamp_cyl_trait_source_recipe_and_run.sql` | The a9 body with the D2 and D3 edits, the `20260928130100` ACL, backfill call                                                                                                                          |
| 3   | `<T>0200_add_cyl_trait_recipe_reads.sql`            | `_cyl_trait_recipe_presence` (the internal D5 presence helper, same grants as the read functions); `get_experiment_traits` replaced; `list_trait_recipes`; `get_trait_recipe_coverage`; `NOTIFY pgrst` |
| 4   | `<T>0300_add_cyl_dataset_recipe_mode.sql`           | `cyl_datasets.recipe_key` with `cyl_datasets_recipe_key_format_check` and backfill; `create_cyl_dataset` replaced; `NOTIFY pgrst`                                                                      |

**Constraint and index names.**

- In migration 1:
  - `cyl_trait_sources_scan_id_fkey` and `cyl_trait_sources_cyl_pipeline_run_id_fkey`, both
    `ON DELETE SET NULL`;
  - `cyl_trait_sources_recipe_key_format_check` and `cyl_trait_sources_recipe_key_version_check`;
  - `cyl_trait_sources_recipe_key_idx`, `cyl_trait_sources_scan_id_idx` and
    `cyl_pipeline_run_scans_argo_workflow_name_idx`.
- In migration 4: `cyl_datasets_recipe_key_format_check`.

Every constraint uses the guarded, named `ADD CONSTRAINT` form from `database-migration.md`, so the
PR-body check lists it.

**Why `ON DELETE SET NULL`.** Scans and runs can be deleted once their trait and run-scan rows are
gone (`test_cyl_experiment_trait_counts.py:973`). A source keeps its provenance.

**Rollback.** The rollbacks are staging hot-apply scripts, worded like
`20260928130000_cyl_writeback_contract_a9_rollback.sql:11-16`. A durable rollback is a new forward
migration.

- **Order.** Apply R4, R3, R2, then R1.
  - R1 raises if a live function still references its objects (writeback spec).
  - R3's header says R4 must run first.
  - The `20260728000000` rollback gets a header note: apply R3 before it.
- **Afterwards,** run `supabase migration repair --status reverted <ts>` for each file, or
  `db push` refuses the checkout.
- **What is lost.** Run stamps written after deploy, and the recipe identity of recipe-mode
  datasets; R4 counts those in a NOTICE. Keys and `scan_id` can be derived again.

## Open Questions

None blocking. Follow-ups: contracts#47 (whether contracts should emit a recipe key) and
contracts#45 (`traits_pipeline_class` for a v2 key).
