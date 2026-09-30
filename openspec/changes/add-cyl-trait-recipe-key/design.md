# Design: recipe key, recipe-aware reads, and run stamping

The normative rules are in the delta specs. This file records why they are the way they are. It
also holds the implementation constraints the specs do not state.

**Terms:**

| Term | Meaning |
|---|---|
| a7 / a9 | sleap-roots-contracts `v0.1.0a7` / `v0.1.0a9` |
| srp#N | talmolab/sleap-roots-pipeline#N |
| Hand-submitted run | An `argo submit` outside Bloom's dispatcher, so it has no `cyl_pipeline_run_scans` rows |
| **Latest** | `get_experiment_traits`' unpinned per-scan `max(source_id)`. This is unchanged, and it can still mix recipes |
| **Default recipe** | The one recipe `list_trait_recipes` proposes for a whole selection (D5) |

## Context

**Base.** The change was checked on 2026-09-29 against:
- Bloom `origin/staging` at `21487acc`, which includes PR #940;
- the staging database, read-only;
- sleap-roots-contracts `v0.1.0a9-2-gbaead42`, whose `identity.py`, `hashing.py` and `models.py`
  are unchanged from the tag.

**Staging, 2026-09-29.**

- **`cyl_trait_sources`** has 85 rows:
  - 5 legacy sources (ids 1–5, `metadata` NULL);
  - 65 a7 pipeline sources (ids 6–203);
  - 15 a9 pipeline sources (ids 219–233).
- **Scan resolution.** Every pipeline source's `inputs.image_ids` resolves through `cyl_images` to
  exactly one scan. For 79 of them, that is the scan their trait rows carry. Id 70 is a `deadbeef`
  verification fixture with no trait rows.
- **Recipes.** The v1 payload gives 10 distinct values over those 80: 9 real recipes and the
  fixture. The 15 a9 sources form one recipe.
- **Unrecorded run fields.** `metadata->>'pipeline_run_id'`, `argo_workflow_uid` and
  `produced_at` are null on all 80.
- **Data shape.**
  - 0 of 40,203 plants lack an accession.
  - 0 experiments lack a species.
  - The server is PostgreSQL 15.14.
- **Function ACLs.**
  - `get_experiment_traits` is executable by `anon` and `service_role`, as well as the four read
    roles.
  - `create_cyl_dataset` is executable by `PUBLIC`, `anon`, `authenticated` and `service_role`, is
    owned by `postgres`, and has `statement_timeout=0`.

**Code facts.**

- **`insert_cyl_result_envelope(jsonb, text)`.** The live body is in
  `20260928130000_cyl_writeback_contract_a9.sql:44`; its ACL is set by `20260928130100`.
  - It inserts the source at the idempotency gate (:127-130) **before** step 6 resolves the scan
    (:203-228).
  - It stores the Provenance object as `metadata`, unchanged.
  - It uses `p_argo_workflow_name` only in the run-scan status updates.
- **`get_experiment_traits(bigint, bigint, text)`.** Defined once, in `20260728000000:31-100`.
  - Its only non-test caller is bloommcp's `SupabaseReader`. That passes a named dict
    (`supabase_reader.py:145-152`) and reads columns by name (`_pivot_wide`).
- **`create_cyl_dataset(text, bigint, bigint, json, json)`.** Defined in `20240904033106:1-48`.
  - bloomctl calls it with five named keys (`bloomcli/src/bloomctl/cyl/datasets.py:311-317`).

## Decisions

### D1. recipe_key v1: the idempotency payload without its per-scan inputs

**What the key covers.** It follows contracts `identity.py:8-61`:
- the model triples `[registry_id, version, weights_checksum]`, sorted, with duplicates kept;
- `predict_code_sha` and `traits_code_sha`;
- `predict_output_params`, only when it is a non-empty object.

It drops `scan_key`, `images_checksum` and `param_hash`. `param_hash` hashes
`{species, mode, age}`, so it varies with plant age.

Two sources share a v1 key exactly when they would have shared an idempotency key, had they been
computed on the same scan with the same params.

**Recorded but deliberately not keyed:**

| Field | Why it is not in the key |
|---|---|
| `contract_version` | a7→a9 was a schema-`$id` restamp, and identity ignores it too |
| `sleap_nn_version` | Implied by `predict_code_sha` |
| Container digests | Empty on some a7 rows. A same-code rebuild must not split a recipe |
| `predict_inference_config` | `device` and `batch_size` are excluded from identity by design |
| Run and orchestration fields | They vary per delivery |

**An age-window model switch inside one experiment is a different recipe.** eberrigan decided
this on 2026-09-28. Models come from a moving W&B `production` registry alias, so only the
resolved models prove "same models".

**Two helpers, one definition.**
- `cyl_trait_recipe_payload_v1(jsonb) RETURNS jsonb` builds the payload.
- `cyl_trait_recipe_key_v1(jsonb) RETURNS text` is
  `encode(sha256(convert_to(payload::text, 'UTF8')), 'hex')`.
- The payload function is what `list_trait_recipes` returns as `definition`. So a pipeline recipe's
  `definition` always hashes to its key, and a test asserts this.

**How the helpers behave:**
- Both are `IMMUTABLE`, and both return NULL for NULL input or a non-object input.
- **They never raise.**
  - The write-back RPC does not validate these fields.
  - A raise would reject envelopes the RPC accepts today.
  - A raise in the backfill would abort the deploy, as the a7→a9 cutover guard did (bloom#685).
- **Missing or odd shapes fall back to defaults instead of raising:**
  - a non-array `predict_models` contributes no triples;
  - a non-object entry contributes a triple of nulls (`->>` on a non-object is NULL);
  - a non-object or empty `predict_output_params` is omitted.

**Sorting, hashing and why not Python's bytes.**
- Triples are ordered by `jsonb::text COLLATE "C"`. The default collation is locale-dependent, and
  the key's only guarantee is determinism. `20260911082453:388` is the precedent for `COLLATE "C"`.
- `sha256()` is the PG11+ built-in. pgcrypto's `digest()` would not resolve under the RPC's
  `search_path = pg_catalog, public, pg_temp`.
- The key is **not** byte-equal to the value Python's `canonical_json` produces. `jsonb::text`
  orders keys by length then bytes, and keeps numeric scale. Only Bloom compares recipe keys
  (contracts#47 asks whether contracts should own one).
- A partition test instead checks the property that matters. Two contracts-valid Provenances share
  a Bloom key exactly when their idempotency payloads, minus the three per-scan inputs, are equal.
- Numeric-scale differences (`0.2` vs `0.20`) are a documented, tested divergence. Predict always
  emits `{"peak_threshold": 0.2}` today.

**Grants.** Both helpers get `REVOKE EXECUTE … FROM PUBLIC, anon`, then `GRANT` to `bloom_agent,
bloom_user, bloom_admin, authenticated`. The read RPCs are `SECURITY INVOKER` and call the payload
helper, so the four read roles need it. The functions are pure, so granting them exposes nothing.

**Versioning.** A redefinition becomes `recipe_key_version = 2`, with new helpers. v1 values are
never rewritten, because exports cite them.

**Key encodings:**

| Kind | `recipe_key` value | Stored? |
|---|---|---|
| Pipeline | 64 lowercase hex characters | Yes |
| Legacy | `legacy:<id>` | Yes |
| Unattributed | `unattributed` | No: it exists only at read time |

`recipe_kind` is derived from the prefix.

**Known blind spot.** The traits step picks a sleap-roots Pipeline class by species, mode and age
window, and provenance does not record which. Neither the idempotency key nor v1 sees that choice
except through `traits_code_sha` and age. contracts#45 (`traits_pipeline_class`) would expose it;
a v2 key could include it.

### D2. scan_id is set after scan resolution, in the same transaction

The source row is inserted at the idempotency gate, before the scan is known. Moving scan
resolution ahead of the gate would break the rule that on a no-op the scan of record governs, not
this delivery's `image_ids`.

So the fresh-insert path runs `UPDATE cyl_trait_sources SET scan_id = v_scan_id WHERE id =
v_source_id` right after step 6 (:228). The other four columns are known before the gate and go in
the `INSERT`.

The main spec's "Write-back is idempotent and provenance-immutable" covers `metadata`, `name` and
`idempotency_key`. These derived columns are written once, by the creating delivery or the
backfill. The writeback delta says so explicitly.

### D3. Run and Workflow stamping reads the dispatcher's record

- `argo_workflow_name` is `p_argo_workflow_name`.
- `cyl_pipeline_run_id` is the single distinct `run_id` among `cyl_pipeline_run_scans` rows with
  that Workflow name. It is NULL when there are none, or more than one.
  - More than one is possible if Argo's generated name suffix repeats after its one-hour TTL
    deletion. NULL is better than a guess.
- The lookup does not need a row for this scan, so an unrequested scan's source still records its
  run.
- A new index on `cyl_pipeline_run_scans(argo_workflow_name)` serves the lookup.

**Column name.** The column is `cyl_pipeline_run_id` (eberrigan, 2026-09-29), because
`pipeline_run_id` already means two other things:
- the producer's text id: `metadata->>'pipeline_run_id'`, the `cyl_scan_traits_source` column, and
  the `run_id_` pin;
- the workflows API response field for Bloom's integer run id
  (`services/workflows/README.md:182`). That integer is the value `cyl_pipeline_run_id` stores, and
  that README gets a note saying so.

**No-op re-deliveries stamp nothing.** The stamps belong to the delivery that created the source.

**No backfill.** Nothing recorded which Workflow wrote an existing source. The two run-scan rows
that carry a `source_id` can't serve as the record: the bloom#875 re-delivery fallback stamps
`source_id` onto a re-delivering Workflow's row too.

### D4. Backfill, and closing the gap between migrations

**Migration 1 creates `cyl_backfill_trait_source_recipe_identity()`**, a `SECURITY INVOKER`
function owned by `postgres` with `EXECUTE` revoked from every non-owner role. Migration 1 calls it.
**Migration 2 calls it again.**

Why migration 2 calls it again: `supabase db push` commits each file separately. A write-back that
lands after migration 1 commits, but before migration 2 does, runs the old a9 body and leaves NULL
columns. Re-running the backfill closes that gap, and one function keeps the two passes identical.

It updates only rows whose columns are NULL, so re-running it is a no-op. Its rules are in the
writeback spec. Two implementation points:

- **Scan resolution guards its inputs.** It uses the RPC's full rule: every element numeric
  (`~ '^[0-9]+$'`), every element matching a `cyl_images` row with a non-null `scan_id`, and
  exactly one distinct scan. The `~` guard comes before any `::bigint`, and a non-array
  `image_ids` counts as unresolvable. Any failure leaves `scan_id` NULL, and the count is reported
  with `RAISE NOTICE`.
- **Nothing reads `cyl_scan_traits`**, which has 28.9M rows and no index leading with
  `source_id`. The "backfilled `scan_id` agrees with trait rows" property is checked in tests and
  in the pre-merge staging dry run, not in the migration.

### D5. Recipe presence, selections and the default recipe

**Presence is defined by trait rows.** A scan *has* recipe K when it has at least one
`cyl_scan_traits` row whose source's `recipe_key` is K, or whose `source_id` is NULL when K is
`unattributed`. Its source of K is the highest such `source_id`.

That definition is shared by `list_trait_recipes`, `get_trait_recipe_coverage`,
`get_experiment_traits(recipe_key_)` and `create_cyl_dataset(recipe_key)`, so all four agree. A
source with no trait rows, such as fixture 70 or an envelope with empty `traits`, contributes no
recipe.

**How presence is found (implementation constraint).** For each selected scan, the candidates are:
- the sources with `scan_id` = that scan, via the new index;
- every source with `scan_id IS NULL`: the legacy sources, plus any pipeline source whose backfill
  could not resolve a scan.

Each candidate, and the NULL source, is confirmed by
`LATERAL (SELECT 1 FROM cyl_scan_traits WHERE scan_id = s AND source_id = c LIMIT 1)`, which the
`(scan_id, source_id, trait_id)` unique index serves.

This form was measured on staging on 2026-09-29, read-only, for experiment 1 (18,471 scans, 5
legacy sources): **450 ms**. The equivalent `EXISTS` was planned as a hash aggregate over all
28.9M `cyl_scan_traits` rows and took **7.0 s**, against PostgREST's 8 s limit. A unit test pins
the `LATERAL … LIMIT 1` text in the migration, because `EXPLAIN` cannot see inside plpgsql.

**Accepted gap.** A break-glass `bloom_admin` `UPDATE` of `cyl_scan_traits.scan_id` that moves a
pipeline source's rows to a different scan hides them from these functions. The write-back RPC
never does that.

**The default recipe** is the one whose highest `source_id` within the selection is highest, i.e.
the most recently written. `unattributed` has no `source_id` and ranks last. It is not "covers the
most scans"; `n_scans` lets a caller choose on coverage instead.

**Why the functions are shaped as they are.**
- The scan universe reuses `get_experiment_traits`' join chain, so coverage and reads agree on
  which scans exist.
- `list_trait_recipes` and coverage take arrays so that one selection can span experiments.
- `get_experiment_traits` stays per-experiment: bloommcp and every other caller read one
  experiment. A multi-experiment export makes one call per experiment, with the same `recipe_key_`.
- `list_experiment_trait_sources` is unchanged. It lists sources, which for pipeline data means
  one per scan. Callers wanting a coherent set use `list_trait_recipes`, and the docs say so.

### D6. Replacing two functions in place, not expand/contract

`.claude/commands/database-migration.md:130-136` asks for expand/contract on RPC signature
changes: add the new form, migrate callers, drop the old form later. It is not followed for
`get_experiment_traits` and `create_cyl_dataset`, for three reasons:

- **The two forms cannot coexist.** The new arguments have defaults, so leaving the old form in
  place makes a named call such as bloommcp's `{experiment_id_, source_id_, run_id_}` match both.
  That fails with PGRST203. It is why `20260701000000` dropped the 2-argument `get_scan_traits`.
- **The callers don't need to migrate.** Every caller passes named arguments and reads columns by
  name: bloommcp, and bloomctl's `create_cyl_dataset` call. No TypeScript code outside the
  generated types references either function.
- **It is safe inside one transaction.** Each migration drops and recreates in one transaction,
  which `db push` commits once, and `NOTIFY pgrst` reloads the schema at commit.

The alternative, a new function name, would leave two read paths to keep in parity forever.

Each migration uses `DROP FUNCTION IF EXISTS` followed by `CREATE OR REPLACE`, so re-running it is
safe.

**ACLs.**
- `get_experiment_traits` is recreated with `REVOKE … FROM PUBLIC, anon` and the four read-role
  grants. That tightens `anon`, as the proposal notes. `service_role` keeps its Supabase default.
- `create_cyl_dataset` is recreated with its current staging ACL, re-granted explicitly, so that
  nothing changes silently. A test compares it with the pre-change ACL.

### D7. Datasets

- `cyl_datasets.recipe_key` and its backfill live in **migration 4**, with the function that uses
  them, so each rollback undoes exactly its own migration.
- There is no default recipe for datasets. A dataset is a reproducibility artifact, so the caller
  names the recipe.
- A PostgREST recipe-mode call must still send `trait_source_id: null`, `qc_set_name` and
  `timepoints`, because only the new trailing argument has a default. The docs say so.
- The argument is `recipe_key`, with no trailing underscore, matching this function's own naming.
  The read functions use `recipe_key_`.
- Excluded scans are not stored. The frozen rows are the record of what the dataset holds.
- The dataset's scan set keeps today's `cyl_scans_extended` filter. That view inner-joins
  experiments to `species` but not plants to `accessions`, so it can differ from
  `get_experiment_traits`' set in both directions. On staging (2026-09-29) neither case occurs.
  Changing a dataset's scan set is out of scope.

### D8. Export sidecar v1

- **What ships in this PR, all under `_WIKI/SUPABASE/`:**
  - `trait-recipes.md`, the reader-facing page;
  - `trait-recipes.export.schema.json`, a JSON Schema (draft 2020-12);
  - `trait-recipes.export.example.json`.

  `lint_migration_isolation` allows any file under `_WIKI/`.
- **How it is checked here.** The root test environment has no `jsonschema` package, and adding
  one means editing the root `pyproject.toml`, which is outside the migration surface. So a unit
  test checks three things instead:
  - the schema parses;
  - the example carries every `required` property at every level;
  - every property is either mapped in the page's field table to a column of the three RPCs (or to
    `cyl_trait_sources.metadata` read by `source_id`), or marked producer-supplied.

  Full validation of the example against the schema lands with the first producer, #865 or #481.
- **What the files are.** `<stem>.csv` has one header row and no `#` lines, so `pandas.read_csv`
  reads it, with `recipe_key` and `source_id` columns. `<stem>.export.json` is the sidecar.
  `<stem>.excluded.csv` is optional.

### D9. No unrequested run-scan rows

#937 step 3 would insert a `requested = false` row for each scan that write-back delivers outside
its run's request.

It is dropped (eberrigan, 2026-09-29). Its cause is the srp#71 `run_manifest.json` union, and PR
#940 (`1bc3056c`, merged 2026-09-29) removes that once the cluster templates pin a bloomctl image
that includes it. Bloom's live-template fixtures still pinned `bloomctl:sha-28034f6` on 2026-09-29.

It would also need a `services/workflows/status_poller.py` change, because `done_count` counts
every `written` or `reused` row (:212, :272). That change is outside the migration surface.

D3 still records the run on an unrequested scan's source. Until the re-pin, bloomctl keeps
printing its misleading non-retriable "failed" line for such a scan.

### D10. No recipe retirement

After a contract or image rollback, the bad build's sources keep the highest `source_id`
(`openspec/changes/archive/2026-09-28-repin-cyl-contract-a9/design.md` § Rollback), so the default
recipe keeps picking it. A `retired_at` flag would fix that.

It is deferred (eberrigan, 2026-09-29): it has never been needed, and adding it later means one
table and a change to D5's default rule. The workaround is an explicit `recipe_key_`.

### D11. Spec deltas and archive hazards

- **cyl-trait-writeback: ADDED only.** The stale, unarchived `fix-cyl-pipeline-run-scan-status`
  MODIFIES "Write-back RPC ingests a ResultEnvelope" with text that predates the bloom#875
  fallback (`archive/2026-09-18-fix-cyl-redelivery-status-fallback/design.md:204-214`). So the new
  requirement states the behavior it preserves inline and does not cite that requirement's text.
- **cyl-trait-read.**
  - It MODIFIES "Bulk experiment-scoped trait reads" and "Additive, non-destructive bulk-read
    migration", whose rollback scenario would otherwise be false.
  - It leaves "Bulk trait read functions are callable by the read roles" unmodified. That
    requirement stays true: the functions are still `SECURITY INVOKER` and callable by the four
    read roles. The new grants requirement adds only the `anon` revoke and covers the new
    functions.
  - No active change touches these requirements.
  - `fix-cyl-scan-traits-latest-rollup`'s MODIFIED "Aggregate experiment summary counts" names
    `get_experiment_traits(experiment_id_, source_id_, run_id_)`. That remains a valid named call.
- **cyl-datasets: new.**
- `--strict` reads only a requirement's first physical line for SHALL or MUST, so every requirement
  opens with a one-line normative sentence.

## Risks / Trade-offs

- **Migration 1 takes an ACCESS EXCLUSIVE lock** on `cyl_trait_sources` while trait reads, which
  join it, are running. It sets `SET LOCAL lock_timeout = '5s'`, following `20260924120000:52`:
  better a retried deploy than a stalled write-back. Migration 4 does the same for
  `cyl_datasets`.
- **Prod data has not been measured.** The staging figures are staging-only. Task 8.0 runs the
  backfill's resolution counts read-only on prod before the staging-to-main promotion.
- **Legacy source 4 (`test`)** becomes the default recipe `legacy:4` on experiment 7206207, with
  values identical to source 2. The data fix is a separate decision for eberrigan and Benfica
  (@blm3886).

## Migration Plan

**Timestamps.** Four files, `<T>0000`, `<T>0100`, `<T>0200` and `<T>0300`, each with a
`supabase/rollbacks/` partner. `<T>` must be greater than staging's newest migration when pushed
(`20260929204846` on 2026-09-29), and it is re-checked with `scripts/lint_migrations.sh` just
before the merge. Tests locate the files by glob, not by timestamp, so a restamp touches only the
file names.

| # | Migration | Contents |
|---|---|---|
| 1 | `…_add_cyl_trait_recipe_key.sql` | Two helpers and the backfill function; five columns; named constraints (below); three indexes; backfill call; `NOTIFY pgrst` |
| 2 | `…_stamp_cyl_trait_source_recipe_and_run.sql` | The a9 body with the D2/D3 edits; the `20260928130100` ACL re-asserted; backfill call |
| 3 | `…_add_cyl_trait_recipe_reads.sql` | `get_experiment_traits` replaced; the two new functions; `NOTIFY pgrst` |
| 4 | `…_add_cyl_dataset_recipe_mode.sql` | `cyl_datasets.recipe_key` with its CHECK and backfill; `create_cyl_dataset` replaced; `NOTIFY pgrst` |

**Named constraints and indexes** (migration 1):
- `cyl_trait_sources_scan_id_fkey`: `ON DELETE SET NULL`;
- `cyl_trait_sources_cyl_pipeline_run_id_fkey`: `ON DELETE SET NULL`;
- `cyl_trait_sources_recipe_key_format_check`;
- `cyl_trait_sources_recipe_key_version_check`;
- `cyl_trait_sources_recipe_key_idx`, `cyl_trait_sources_scan_id_idx`,
  `cyl_pipeline_run_scans_argo_workflow_name_idx`.

**Why `ON DELETE SET NULL`.** Scans and runs can be deleted today once their trait rows are gone
(`test_cyl_experiment_trait_counts.py:973`; the dispatch tests' cleanup). A source keeps its
provenance when its scan or run goes.

**Constraint forms.** Constraints use the guarded named forms in `database-migration.md`, so the
PR-body check lists them.

**Rollback.** The rollbacks are staging hot-apply scripts; a durable rollback is a new forward
migration. The same wording is used as `20260928130000_cyl_writeback_contract_a9_rollback.sql:11-16`.

- **Order.** Apply R4, R3, R2, then R1.
- **Each rollback restores its base exactly:**
  - R2: the a9 body, with the `20260928130100` ACL;
  - R3: the `20260728000000` function and grants;
  - R4: the `20240904033106` function, its prior ACL, and dropping the column.
- **Guards:**
  - R1 raises if any live function body still references its columns, because plpgsql does not
    track that dependency.
  - R4 raises NOTICE with the number of recipe-mode datasets, whose only identity it drops.
- **After a hand-applied rollback,** run `supabase migration repair --status reverted <ts>` for each
  one. Otherwise `db push` refuses the checkout that no longer has those files.
- **Losses.** Stamps written after deploy are lost, and so is the recipe identity of recipe-mode
  datasets. Keys and `scan_id` can be derived again.

## Open Questions

None blocking. Follow-ups: contracts#47 (should contracts emit a recipe key?) and contracts#45 (a
v2 key could include `traits_pipeline_class`).
