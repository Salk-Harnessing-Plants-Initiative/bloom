# Design: recipe key, recipe-aware reads, and run stamping

## Context

**Base.** The change was checked against:
- Bloom `origin/staging` at `21487acc`, which includes PR #940;
- the staging database, read-only, on 2026-09-29;
- `sleap-roots-contracts` at `v0.1.0a9-2-gbaead42`. Its `identity.py`, `hashing.py` and
  `models.py` are unchanged from the `v0.1.0a9` tag.

This design grew out of a draft analysis from 2026-09-28 (bloom#935's evidence). Every fact
below was re-measured for this change; where the draft differed, this document wins.

**Staging, 2026-09-29.**

- **`cyl_trait_sources`** has 85 rows:
  - 5 legacy sources (ids 1–5, `metadata` NULL);
  - 65 a7 pipeline sources (ids 6–203);
  - 15 a9 pipeline sources (ids 219–233).
- **Scan resolution.** For every pipeline source, `metadata->'inputs'->'image_ids'` resolves
  through `cyl_images` to exactly one scan. For 79 of them, that is the scan their trait rows
  carry. Id 70 is a `deadbeef` fixture with no trait rows.
- **Recipes.** Computing the v1 payload below over all 80 pipeline sources gives 10 distinct
  values: 9 real recipes and the fixture. The 15 a9 sources form a single recipe.
- **Unrecorded provenance.** `metadata->>'pipeline_run_id'`, `argo_workflow_uid` and
  `produced_at` are null on all 80.
- **Run-scan rows.** `cyl_pipeline_run_scans` has 30 rows. 24 carry an `argo_workflow_name`;
  2 carry a `source_id`.
- **Platform.** PostgreSQL 15.14, with pgcrypto installed.

**Relevant code facts.**

- **`insert_cyl_result_envelope(jsonb, text)`.** The live body is in
  `20260928130000_cyl_writeback_contract_a9.sql:44`.
  - It inserts the source row (:126-130) **before** it resolves the scan (:203-228).
  - It stores the whole provenance object as `metadata`, unchanged.
  - It reads `p_argo_workflow_name` only in the run-scan status updates (:155-193, :279-305).
- **`get_experiment_traits(bigint, bigint, text)`.** Defined once, in
  `20260728000000_get_experiment_traits.sql:31-100`, as `SECURITY INVOKER`.
  - Its only non-test caller is bloommcp's `SupabaseReader.load_experiment`. That passes a
    named dict (`supabase_reader.py:145-152`) and reads result columns by name
    (`_pivot_wide`, :390-514).
- **`create_cyl_dataset(text, bigint, bigint, json, json)`.** Defined in
  `20240904033106_create_fix_create_cyl_dataset_function_again.sql:2-48`, as `SECURITY INVOKER`
  with a function-level `statement_timeout = 0`.
  - It freezes one `trait_source_id`'s rows into `cyl_dataset_traits`.
  - bloomctl calls it with the named keys `{name, experiment_id, trait_source_id, qc_set_name,
    timepoints}` (`bloomcli/src/bloomctl/cyl/datasets.py:311-317`).

## Goals / Non-Goals

**Goals:**
- Every trait source carries a stored, versioned recipe identity. Existing sources are
  backfilled.
- A caller can list the recipes in a scan selection and see per-scan coverage for one recipe.
- A caller can read exactly one recipe's traits.
- New sources record the Workflow and the Bloom run that wrote them.
- A dataset can be frozen from a recipe, and it records which recipe.

**Non-Goals:**
- Unrequested run-scan rows (#937 step 3). See D9.
- Retiring recipes. See D10.
- Consumer UIs and CLIs: #865, #482, #481.
- bloommcp reader changes, including #936.

## Decisions

### D1. recipe_key v1 = the idempotency payload minus the per-scan inputs

**The payload.** It follows the contracts code at `identity.py:8-61`:

```text
{ "models": sorted([[registry_id, version, weights_checksum], ...]),
  "predict_code_sha": ..., "traits_code_sha": ...,
  "predict_output_params": {...} }   -- key present only when non-null and non-empty
```

It keeps what contracts keeps:
- **Bare triples.** `root_type` and `sleap_nn_version` are not in the key.
- **Duplicates.** A model that serves two root types appears twice.
- **The truthy gate on `predict_output_params`.** `null`, a missing key and `{}` all leave it
  out.

**What it leaves out:** the three per-scan inputs, `scan_key`, `images_checksum` and
`param_hash`. `param_hash` hashes `{species, mode, age}`, so it varies with plant age.

Two sources share a v1 key exactly when they would have shared an idempotency key had they been
computed on the same scan with the same params.

**Fields recorded but not keyed:**

| Field | Why it is not in the key |
|---|---|
| `contract_version` | a7→a9 was an `$id`-only restamp, and identity ignores it too |
| `sleap_nn_version` | Implied by `predict_code_sha` |
| Both container digests | Empty on some a7 rows. A same-code rebuild must not split a recipe |
| `predict_inference_config` | `device` and `batch_size` are excluded from identity by design |

**User decision, 2026-09-28: an age-window model switch inside one experiment is a different
recipe.** Models come from a moving W&B `production` alias at runtime, so only the resolved
models prove "same models".

**Serialization.** The key is computed in SQL:
`encode(sha256(convert_to(payload::text, 'UTF8')), 'hex')`, using the built-in `sha256()`.

- It uses the built-in because the RPC's `search_path` is `pg_catalog, public, pg_temp`, so
  pgcrypto's `digest()` would not resolve.
- The triples are ordered by their own `jsonb::text` form.
- **It is not byte-equal to the value Python's `canonical_json` would produce,** and it doesn't
  need to be: only Bloom compares recipe keys (contracts#47 asks whether contracts should own a
  `compute_recipe_key`). Two differences:
  - `jsonb::text` orders keys by length first, then bytewise.
  - It keeps numeric scale.
- The property it guarantees is determinism for a given stored `metadata`, pinned by
  `recipe_key_version`.

A later redefinition is `recipe_key_version = 2`, computed by a new helper. v1 values are never
rewritten, because old exports cite them.

**Encoding in the column:**

| Kind | `recipe_key` value |
|---|---|
| Pipeline recipe | 64 lowercase hex characters |
| Legacy source | `legacy:<source_id>` (`metadata IS NULL`) |
| Unattributed | `unattributed`, for trait rows whose `source_id` is NULL |

The prefixes cannot collide with hex. `unattributed` exists only at read time, because there is
no source row to store it on. `recipe_key_version` is 1 for both stored forms.

**One helper.** `cyl_trait_recipe_key_v1(metadata jsonb) RETURNS text` is `IMMUTABLE`, returns
NULL for NULL input, and lives in `public`. EXECUTE is revoked from `PUBLIC, anon,
authenticated`: only migrations and the `SECURITY DEFINER` RPC, which runs as `postgres`, call
it. The backfill and the RPC both call it, so the definition lives in one place.

A generated column was rejected. Ordering the model array needs a subquery, and the RPC being the
sole writer is already the pattern here.

**Known blind spot.** The traits step picks a sleap-roots Pipeline class by species, mode and age
window, and that choice is not in provenance. Neither the idempotency key nor v1 can see it
directly; each captures it only through `traits_code_sha` and age. talmolab/sleap-roots-contracts#45
(`traits_pipeline_class`) would expose it. A v2 key could include it once producers emit it.

### D2. scan_id is set after scan resolution, in the same call

The source row is inserted at the atomic gate, before the scan is known. Moving scan resolution
ahead of the gate would change a documented rule: on a no-op, the scan of record governs, not
this delivery's `image_ids` ("Write-back is idempotent and provenance-immutable").

So the fresh-insert path runs one `UPDATE cyl_trait_sources SET scan_id = v_scan_id WHERE id =
v_source_id` right after step 7. It is the same transaction, so a failure rolls back both
statements.

`recipe_key`, `recipe_key_version`, `argo_workflow_name` and `cyl_pipeline_run_id` are all known
before the gate, so they go in the `INSERT` itself.

### D3. Run and Workflow stamping reads the dispatcher's own record

On a fresh insert:
- `argo_workflow_name := p_argo_workflow_name`.
- `cyl_pipeline_run_id := (SELECT DISTINCT run_id FROM cyl_pipeline_run_scans WHERE
  argo_workflow_name = p_argo_workflow_name)`, when that query yields exactly one row; otherwise
  NULL.

The lookup does not require a row for *this* scan, so a scan the run did not request still gets
its run. More than one run is possible only if Argo's generated suffix repeats after
TTL deletion. In that case NULL is safer than guessing.

A new index, `cyl_pipeline_run_scans(argo_workflow_name)`, keeps the lookup cheap; none exists
today.

**Column name.** The column is `cyl_pipeline_run_id`, not `pipeline_run_id` (user decision,
2026-09-29). `pipeline_run_id` already means the producer's text id: `metadata->>'pipeline_run_id'`,
the `cyl_scan_traits_source.pipeline_run_id` column, and the `run_id_` pin argument.

**Hand-submitted runs** have no run-scan rows. They get `argo_workflow_name` and a NULL run.
Calls with no Workflow name (manual `bloomctl cyl ingest-result`) get NULL for both.

**No-op re-delivery writes none of these.** The stamps belong to the delivery that created the
source. That follows the existing immutability rule, under which a no-op writes no further source
rows.

**No backfill for this decision.** Nothing recorded which Workflow wrote an existing source.
The two run-scan rows that carry a `source_id` can't be used: the bloom#875 fallback also stamps
`source_id` onto a *re-delivering* Workflow's row, so those rows don't prove authorship.

### D4. Backfill

In the schema migration:

- **`recipe_key`** is set to `cyl_trait_recipe_key_v1(metadata)` where `metadata IS NOT NULL`,
  and to `'legacy:' || id` where it is NULL.
- **`recipe_key_version`** is set to 1 on every row.
- **`scan_id`** is set for pipeline sources only, using the RPC's resolution rule: the distinct
  non-null `cyl_images.scan_id` of `metadata->'inputs'->'image_ids'`, applied only when there is
  exactly one. Anything else stays NULL, and the migration `RAISE NOTICE`s the count.
  - Staging expects 80 of 80 resolved.
  - A test asserts that this agrees with the trait rows wherever trait rows exist.
- **Legacy sources keep a NULL `scan_id`**, because each covers many scans.

The migration has to be safe to re-run: the updates are `WHERE recipe_key IS NULL` and
`WHERE scan_id IS NULL`.

### D5. Read surface and the definition of "newest"

**Selection.** A selection is a set of scans:
- scans of `experiment_ids_`, intersected with `scan_ids_` when both are given;
- `scan_ids_` alone when only it is given;
- an error when both are NULL.

The scan universe uses `get_experiment_traits`' join chain, which is
experiments → waves → plants → accessions (inner) → scans. So coverage counts agree with what
the trait read can return.

**A scan's available recipes:**
- **Pipeline:** sources `WHERE scan_id = s`, via the new index on `cyl_trait_sources(scan_id)`.
- **Legacy:** one probe per legacy source, `LATERAL (SELECT 1 FROM cyl_scan_traits WHERE
  scan_id = s AND source_id = L LIMIT 1)`, served by the `(scan_id, source_id, trait_id)`
  unique index.
- **`unattributed`:** the same probe with `source_id IS NULL`.

This form was measured on staging on 2026-09-29, read-only, for experiment 1 (18,471 scans,
5 legacy sources): **450 ms**. The equivalent `EXISTS` semi-join was planned as a hash aggregate
over all 28.9M `cyl_scan_traits` rows and took **7.0 s**, against PostgREST's 8 s limit. The
implementation MUST use the `LATERAL … LIMIT 1` form. A task re-measures it on staging.

**Default ("newest").** The default is the recipe whose highest `source_id` within the selection
is highest.
- `unattributed` has no `source_id`, so it ranks last.
- Ties cannot happen, because a source belongs to exactly one recipe.
- This is "most recently written". It is not "covers the most scans". `list_trait_recipes`
  returns `n_scans`, so a caller can choose by coverage instead.

**`list_trait_recipes(experiment_ids_ bigint[] DEFAULT NULL, scan_ids_ bigint[] DEFAULT NULL)`**
returns one row per recipe present in the selection:

| Column | Content |
|---|---|
| `recipe_key` | The key |
| `recipe_key_version` | The key version |
| `recipe_kind` | `pipeline`, `legacy` or `unattributed` |
| `definition jsonb` | Pipeline: the D1 payload from the newest source's `metadata`. Legacy: `{source_id, source_name}`. `unattributed`: NULL |
| `n_scans` | Scans in the selection with this recipe |
| `newest_source_id` | Highest `source_id` of this recipe in the selection |
| `is_default` | True for exactly one row, the default |

**`get_trait_recipe_coverage(experiment_ids_ bigint[] DEFAULT NULL, scan_ids_ bigint[] DEFAULT
NULL, recipe_key_ text DEFAULT NULL)`** returns one row per selected scan. With `recipe_key_`
NULL it uses the default. Columns: `scan_id`, `experiment_id`, `plant_qr_code`, `recipe_key`
(the one evaluated), `status`, `source_id` (the source used when `included`, else NULL) and
`available_recipes text[]`.

Status rules, with the first match winning:

| Status | Rule |
|---|---|
| `included` | The scan has a source with the evaluated recipe. `source_id` is its highest such source |
| `no_traits` | The scan has no trait rows at all |
| `legacy_only` | Every recipe the scan has is `legacy:*` or `unattributed`, and none is the evaluated one |
| `other_recipe` | Anything else |

A `recipe_key_` that matches no stored source and is not `unattributed` raises an error. That
catches typos. A key that exists but is absent from the selection simply includes no scans.

**`get_experiment_traits(experiment_id_, source_id_, run_id_, recipe_key_ text DEFAULT NULL,
scan_ids_ bigint[] DEFAULT NULL)`:**
- It returns the existing 11 columns plus `recipe_key`, appended last.
- `recipe_key_` is mutually exclusive with `source_id_` and `run_id_`.
- With `recipe_key_`, each scan returns the rows of its highest source with that recipe. For
  `unattributed`, that means its NULL-source rows. Scans without that recipe are omitted.
- `scan_ids_` narrows every mode.
- With every new argument NULL, the behavior is byte-for-byte the current function's. It still
  matches `get_scan_traits` and still underlies `get_experiment_summary_counts`' parity
  requirement.
- `DROP FUNCTION get_experiment_traits(bigint, bigint, text)`, then `CREATE`. Leaving the 3-arg
  form alongside would make a named `{experiment_id_, source_id_, run_id_}` call ambiguous
  (PGRST203). That is the same reason `20260701000000` dropped the 2-arg `get_scan_traits`.

**Grants.** All three read functions are `SECURITY INVOKER`. They get `REVOKE EXECUTE … FROM
PUBLIC, anon`, then `GRANT` to `bloom_agent, bloom_user, bloom_admin, authenticated`. That matches
`20260817150000`'s posture, which revokes `anon` explicitly because Supabase's default privileges
grant it directly.

**Performance boundary.** A recipe read of a large legacy source hits the same 8 s limit as
today. Experiment 1's `legacy:5` alone is 13.9M rows. That is bloom#936's problem (pagination or
a timeout for `bloom_agent`) and is not solved here. The pipeline recipes read by index.

### D6. Datasets record their recipe

- `cyl_datasets.recipe_key text NULL` is added.
- `create_cyl_dataset(name, experiment_id, trait_source_id, qc_set_name, timepoints, recipe_key
  text DEFAULT NULL)`:
  - Exactly one of `trait_source_id` and `recipe_key` is required; zero or both raises an error.
  - **Source mode** behaves as today. `recipe_key` is stored as that source's `recipe_key`, so
    datasets built by source also say what they hold.
  - **Recipe mode** freezes, for each scan matched by the existing experiment, timepoint and QC
    filters, the `cyl_scan_traits` rows of that scan's highest source with the recipe, or its
    NULL-source rows for `unattributed`. `trait_source_id` stays NULL.
  - There is **no default recipe**. A dataset is a reproducibility artifact, so the caller must
    name the recipe.
- `DROP` the 5-arg form, then `CREATE`, because adding an argument changes the signature.
  - The new argument has a default, so bloomctl's named 5-key call binds unchanged.
  - The function-level `SET statement_timeout = 0` and `SECURITY INVOKER` are preserved.
  - No explicit grants exist today. The re-created function gets the same default privileges,
    which a test asserts.
- The frozen rows remain the record of which scans are in the dataset. Excluded scans are not
  stored; the coverage RPC reports them at creation time.
- A new `cyl-datasets` spec documents both modes. Today's behavior has never been specified, and
  it is documented as-is.

### D7. Export sidecar v1: normative text in docs, its fields supplied by the RPCs

The format lives in `_WIKI/SUPABASE/trait-recipes.md`. It has three files:
- **`<stem>.csv`**: one header row and no `#` lines, so default `pandas.read_csv` reads it.
  Metadata columns come first, then `recipe_key` and `source_id`, then the traits.
- **`<stem>.export.json`**: `export_schema_version: 1`, generator, selection, the recipe (its key
  and definition), the fields observed but not keyed, the included scans, the excluded scans with
  reasons and available recipes, and the other recipes in the selection.
- **`<stem>.excluded.csv`**: an optional flat gap list.

A migration PR cannot carry a `.json` schema file, and no producer exists yet. So the spec makes a
checkable claim instead: every sidecar field is supplied by a named column of these RPCs. The
JSON Schema file lands with the first producer, #865 or #481.

### D8. Spec deltas avoid the known archive hazards

- **cyl-trait-writeback: ADDED only.** "Write-back RPC ingests a ResultEnvelope" is MODIFIED by
  the unarchived, stale `fix-cyl-pipeline-run-scan-status`. Its delta predates the bloom#875
  fallback, as `2026-09-18-fix-cyl-redelivery-status-fallback/design.md:204-214` records. A third
  MODIFY would join an existing revert trap. The new requirements state only the added writes and
  cross-reference the existing ones.
- **cyl-trait-read: MODIFIED "Bulk experiment-scoped trait reads".** Its only text was in the
  unarchived `add-bulk-trait-read-rpc`, which is archived in this PR first. No other active change
  touches that requirement. `fix-cyl-scan-traits-latest-rollup` MODIFIES "Canonical source-aware
  trait view" and "Aggregate experiment summary counts"; this change touches neither.
- **cyl-datasets: new capability**, so it has no siblings.
- `openspec validate --strict` reads only a requirement's first physical line for SHALL or MUST.
  Every requirement here therefore opens with a single-line normative sentence.

### D9. Unrequested run-scan rows are not added

#937 proposed inserting a `requested = false` run-scan row whenever write-back delivers a scan
its run did not request. That was decided against on 2026-09-29, for two reasons:

- **Its cause is being removed.** The cause is srp#71: bloomctl's `run_manifest.json` union.
  PR #940 (merged to staging 2026-09-29, `1bc3056c`) names the manifest per run, so write-back's
  scope is the run's own scans.
  - It takes effect once the cluster templates run a bloomctl image that includes it. Bloom's
    live-template fixtures still pin `bloomctl:sha-28034f6`.
- **It would cost more than it fixes.** It needs a `services/workflows/status_poller.py` change.
  The poller counts every `written` row into `done_count` (:212, :272), which would exceed
  `scan_count`. `lint_migration_isolation` would force that change into a second PR.

D3 still records the run on an unrequested scan's source. Revisit only if unrequested envelopes
appear after the re-pin.

### D10. No recipe retirement yet

The a9 design's § Rollback describes the gap. After a contract or image rollback, the bad build's
sources keep the highest `source_id`, because the older build's recompute is a no-op on its
original key. So "newest" keeps choosing the bad build. A `cyl_trait_recipes.retired_at` flag
would let the default skip it.

That has never been needed: the a9 values matched a7 exactly. Adding it later means one table
and a change to D5's default rule, with no data migration. The user chose to defer it on
2026-09-29. Until it exists, the workaround is an explicit `recipe_key_` pick.

## Risks / Trade-offs

- **`get_experiment_traits` return-type change.**
  - Named-argument, by-name callers survive. bloommcp's fake DB ignores unknown parameters, and
    its fixtures lack `recipe_key`; that is harmless, because `_pivot_wide` selects columns
    explicitly.
  - Mitigation: a PostgREST test calls the RPC with bloommcp's exact 3-key named dict.
- **Unrequested envelopes before the re-pin.** An envelope for a scan outside a Bloom run still
  gets `status_update_matched=false` and bloomctl's misleading non-retriable "failed" line. That
  is unchanged by this PR (D9).
- **Workflow-name collisions** give a NULL run rather than a wrong one (D3).
- **Legacy `test` source 4** (experiment 7206207) becomes the default recipe `legacy:4` there,
  with values identical to source 2. It is visible in `list_trait_recipes`. The data fix is a
  separate decision for the user and Benfica.

## Migration Plan

Four forward migrations, each with a `supabase/rollbacks/` partner. Timestamps must be greater
than staging's newest migration at merge time (`20260929204846` on 2026-09-29).

1. **Schema and backfill.**
   - The five `cyl_trait_sources` columns, with their FKs and CHECKs:
     - `recipe_key_version IN (1)` when set;
     - `recipe_key` either matches `^[0-9a-f]{64}$` or `^legacy:[0-9]+$`, or is NULL.
   - Indexes on `cyl_trait_sources(recipe_key)`, `(scan_id)` and
     `cyl_pipeline_run_scans(argo_workflow_name)`.
   - The helper and the D4 backfill.
   - `cyl_datasets.recipe_key`, backfilled from its source's `recipe_key` wherever
     `trait_source_id` is set.
2. **Write-back RPC.** `CREATE OR REPLACE insert_cyl_result_envelope(jsonb, text)`, copied from
   the a9 body with D2 and D3 applied. It re-asserts the owner and the `REVOKE … FROM PUBLIC,
   anon, authenticated` plus the four `GRANT`s.
3. **Read RPCs.** Drop and recreate `get_experiment_traits`, and create `list_trait_recipes` and
   `get_trait_recipe_coverage`.
4. **Datasets.** Drop and recreate `create_cyl_dataset`.

**Rollback.** Apply the rollbacks in reverse order:
- (4) restores the 5-arg dataset function;
- (3) restores the 3-arg body from `20260728000000`;
- (2) restores the a9 body;
- (1) drops the columns, indexes and helper.

Any column data written after deploy is lost on rollback. It is derivable again, except the run
stamps. Rollback (1) has to run last, because (2)–(4) reference its columns.

## Open Questions

None blocking. For follow-up:
- Should contracts emit `recipe_key` (contracts#47)?
- `traits_pipeline_class` for a v2 key (contracts#45).
