## ADDED Requirements

### Requirement: Trait source recipe and run columns

`cyl_trait_sources` SHALL carry nullable `recipe_key text`, `recipe_key_version smallint`, `scan_id bigint`, `argo_workflow_name text` and `cyl_pipeline_run_id bigint` columns (together, the *recipe and run columns*), with `scan_id` referencing `cyl_scans(id)` and `cyl_pipeline_run_id` referencing `cyl_pipeline_runs(id)`, both `ON DELETE SET NULL`.

**Named constraints.** The foreign keys SHALL be named `cyl_trait_sources_scan_id_fkey` and
`cyl_trait_sources_cyl_pipeline_run_id_fkey`. Two CHECK constraints SHALL restrict the new
columns:
- `cyl_trait_sources_recipe_key_format_check`: `recipe_key` is NULL, matches `^[0-9a-f]{64}$`, or
  matches `^legacy:[0-9]+$`;
- `cyl_trait_sources_recipe_key_version_check`: `recipe_key_version` is NULL or `1`.

**Indexes.** `cyl_trait_sources(recipe_key)`, `cyl_trait_sources(scan_id)` and
`cyl_pipeline_run_scans(argo_workflow_name)` SHALL be indexed.

**Access.** None of the recipe and run columns SHALL be readable by `bloom_workflows`. Its
column-scoped `SELECT` on this table SHALL be exactly `(id, metadata, idempotency_key)`.

#### Scenario: A malformed recipe_key is rejected

- **WHEN** a row is written with `recipe_key` set to any of `'unattributed'`, `'legacy:'`,
  `'legacy:-1'`, 64 uppercase hex characters, 63 lowercase hex characters, or a valid key followed
  by a newline
- **THEN** the write is rejected by `cyl_trait_sources_recipe_key_format_check`

#### Scenario: Valid recipe_key forms are accepted

- **WHEN** a row is written with `recipe_key` set to 64 lowercase hex characters, or to
  `'legacy:12'`, with `recipe_key_version = 1`
- **THEN** the write succeeds

#### Scenario: A recipe_key_version other than 1 is rejected

- **WHEN** a row is written with `recipe_key_version = 2`
- **THEN** the write is rejected by `cyl_trait_sources_recipe_key_version_check`

#### Scenario: Deleting a scan or run keeps the source

- **WHEN** a scan whose trait rows have been deleted is itself deleted, or a `cyl_pipeline_runs`
  row with no remaining `cyl_pipeline_run_scans` rows, referenced by a source, is deleted
- **THEN** the delete succeeds, and the source row remains with `scan_id` (respectively
  `cyl_pipeline_run_id`) set to NULL

#### Scenario: bloom_workflows cannot read the new columns

- **WHEN** a session assumes `bloom_workflows` and selects any of `recipe_key`,
  `recipe_key_version`, `scan_id`, `argo_workflow_name` or `cyl_pipeline_run_id` from
  `cyl_trait_sources`
- **THEN** the query fails with insufficient privilege

### Requirement: Recipe key v1 definition

Bloom SHALL compute `recipe_key` v1 with `cyl_trait_recipe_key_v1(jsonb)`, defined as the lowercase hex sha256 of the UTF-8 text of `cyl_trait_recipe_payload_v1(jsonb)`.

**The payload.** For an object argument, `cyl_trait_recipe_payload_v1` SHALL return a jsonb object
with these keys:
- `models`: one `[registry_id, version, weights_checksum]` array per element of
  `predict_models`, taken with `->>` so that a missing field is JSON `null`.
  - The arrays are ordered by their `jsonb::text` under `COLLATE "C"`, with duplicates kept.
  - A non-array `predict_models` yields an empty list.
- `predict_code_sha` and `traits_code_sha`, as text or JSON `null`.
- `predict_output_params`, present only when it is a non-empty jsonb object.

**What the key does not depend on.** It SHALL NOT depend on any other Provenance field. That
includes `scan_key`, `inputs`, `params` (and so `param_hash`), `idempotency_key`,
`contract_version`, `pipeline_run_id`, `worker_request_id`, `argo_workflow_uid`, `argo_node_id`,
`produced_at`, `traits_sleap_roots_version`, both container digests, `predict_inference_config`,
and each model's `root_type` and `sleap_nn_version`.

**How the helpers behave.**
- Both helpers SHALL be `IMMUTABLE` and owned by `postgres`.
- Both SHALL return NULL for NULL or non-object input.
- Neither SHALL raise for any jsonb input.

**Access.** `EXECUTE` on both helpers SHALL be revoked from `PUBLIC` and `anon`, and granted to
`bloom_agent`, `bloom_user`, `bloom_admin` and `authenticated`. `service_role` keeps the
`EXECUTE` it holds through Supabase default privileges.

#### Scenario: The key ignores fields outside the payload

- **WHEN** two Provenance objects differ only in one field outside the payload, for each such
  field in the list above
- **THEN** `cyl_trait_recipe_key_v1` returns the same value for both

#### Scenario: The key changes with any payload input

- **WHEN** two Provenance objects differ in a model's `registry_id`, `version` or
  `weights_checksum` (including `null` versus `""`), in either code sha, or in a non-empty
  `predict_output_params`
- **THEN** `cyl_trait_recipe_key_v1` returns different values

#### Scenario: Model order and empty output params do not change the key

- **WHEN** Provenance objects list the same models in different orders, or carry
  `predict_output_params` as `null`, as `{}`, or not at all
- **THEN** each of those groups yields a single `cyl_trait_recipe_key_v1` value

#### Scenario: A model repeated for two root types is counted twice

- **WHEN** one Provenance lists a model once, and another lists the same triple twice under two
  `root_type` values
- **THEN** the two keys differ

#### Scenario: Odd shapes never raise

- **WHEN** either helper is called with `'{}'`, with no `predict_models`, with `predict_models` as
  `[]`, an object or a string, with a scalar entry in `predict_models`, with an entry missing
  `weights_checksum`, or with `predict_output_params` as a string or an array
- **THEN** each call returns without error, `cyl_trait_recipe_key_v1` returns 64 lowercase hex
  characters, and it returns NULL for `NULL`, `'[]'` and `'"x"'`

#### Scenario: The definition hashes to the key

- **WHEN** `encode(sha256(convert_to(cyl_trait_recipe_payload_v1(m)::text, 'UTF8')), 'hex')` is
  computed for any object `m`
- **THEN** it equals `cyl_trait_recipe_key_v1(m)`

#### Scenario: The key partitions Provenances as contracts identity does

- **WHEN** the committed golden vectors, generated with sleap-roots-contracts `0.1.0a9`, are
  hashed by `cyl_trait_recipe_key_v1`
- **THEN** two vectors share a key exactly when their contracts idempotency payloads, with
  `scan_key`, `images_checksum` and `param_hash` held equal, are equal. The only exceptions are
  vector pairs marked as the documented divergence: an integer versus an integer-valued float
  (`1` versus `1.0`), which contracts' `canonical_json` collapses and jsonb keeps distinct.

### Requirement: Write-back stamps each new source with its recipe, scan, Workflow and run

When a delivery creates a source, `insert_cyl_result_envelope(jsonb, text)` SHALL set that source's recipe and run columns in the same transaction.

**The values:**
- `recipe_key` is `cyl_trait_recipe_key_v1(provenance)`, and `recipe_key_version` is `1`.
- `scan_id` is the scan resolved from `provenance.inputs.image_ids`.
- `argo_workflow_name` is `p_argo_workflow_name`.
- `cyl_pipeline_run_id` is the single distinct `run_id` among `cyl_pipeline_run_scans` rows whose
  `argo_workflow_name` equals `p_argo_workflow_name`. It is NULL when `p_argo_workflow_name` is
  NULL, when no such row exists, or when more than one distinct `run_id` matches. The lookup SHALL
  NOT require a row for the resolved scan.

**On a no-op re-delivery,** the RPC SHALL NOT change any of the recipe and run columns, whatever
`p_argo_workflow_name` it receives. These columns are written once, by the delivery that creates
the source or by the backfill. The provenance-immutability rule continues to cover `metadata`,
`name` and `idempotency_key`.

**Everything else is unchanged.** The RPC SHALL keep:
- the `(jsonb, text)` signature, as its only overload;
- its validation;
- its return value, including `status_update_matched`;
- its run-scan status updates, which on a no-op delivery include the fallback that resolves the
  scan from an existing run-scan row carrying this source's id;
- its `EXECUTE` grants: revoked from `PUBLIC`, `anon` and `authenticated`, and granted to
  `bloom_writer`, `service_role`, `bloom_admin` and `bloom_workflows`.

It SHALL NOT insert `cyl_pipeline_run_scans` rows.

#### Scenario: A fresh delivery records its recipe and scan

- **WHEN** a valid envelope is ingested for the first time
- **THEN** its new source has `recipe_key` equal to `cyl_trait_recipe_key_v1(metadata)`,
  `recipe_key_version = 1`, and `scan_id` equal to the `scan_id` the call returns

#### Scenario: A Bloom-dispatched delivery records its Workflow and run

- **WHEN** an envelope is ingested with `p_argo_workflow_name` set to Workflow `W`, and `W`
  appears on `cyl_pipeline_run_scans` rows of exactly one run `R`
- **THEN** the new source has `argo_workflow_name = W` and `cyl_pipeline_run_id = R`

#### Scenario: An unrequested scan still records its run

- **WHEN** an envelope for scan `S` is ingested under Workflow `W`, `W` belongs to run `R`, and `R`
  has no run-scan row for `S`
- **THEN** the new source has `cyl_pipeline_run_id = R`, the number of `cyl_pipeline_run_scans`
  rows is unchanged, and `status_update_matched` is `false`

#### Scenario: A hand-submitted delivery records only its Workflow

- **WHEN** an envelope is ingested with `p_argo_workflow_name` set to `W`, and no
  `cyl_pipeline_run_scans` row carries `W`
- **THEN** the new source has `argo_workflow_name = W` and `cyl_pipeline_run_id` NULL

#### Scenario: An ambiguous Workflow name records no run

- **WHEN** `cyl_pipeline_run_scans` rows of two different runs both carry the ingesting
  `p_argo_workflow_name`
- **THEN** the new source has `cyl_pipeline_run_id` NULL

#### Scenario: A delivery with no Workflow name records neither

- **WHEN** an envelope is ingested with `p_argo_workflow_name` NULL
- **THEN** the new source has `argo_workflow_name` and `cyl_pipeline_run_id` NULL, and its
  `recipe_key` and `scan_id` set

#### Scenario: A no-op re-delivery leaves the stamps alone

- **WHEN** an already-ingested envelope is delivered again under a different
  `p_argo_workflow_name`
- **THEN** the existing source's recipe and run columns are unchanged

#### Scenario: A failed delivery leaves no source

- **WHEN** a delivery fails on an unresolvable `image_ids`, a non-scan-grain trait, or a
  non-integer blob `file_size`
- **THEN** the call raises, and no `cyl_trait_sources` row exists for its `idempotency_key`

#### Scenario: The re-delivery fallback still works

- **WHEN** an envelope first ingested under Workflow `wf-a` is re-delivered under `wf-b`, and
  `wf-b` has a queued row for the same scan
- **THEN** `status_update_matched` is `true` and the `wf-b` row is `'written'` with the original
  `source_id`

### Requirement: Existing trait sources are backfilled with recipe identity

Bloom SHALL provide `cyl_backfill_trait_source_recipe_identity()`, which sets `recipe_key`, `recipe_key_version` and `scan_id` wherever they are NULL on `cyl_trait_sources`, and the recipe-identity and write-back migrations SHALL each call it.

**What the backfill sets:**
- `recipe_key` is `cyl_trait_recipe_key_v1(metadata)` when `metadata` is a jsonb object, and
  `'legacy:' || id` otherwise (NULL or any non-object `metadata`).
- `recipe_key_version` is `1` wherever `recipe_key` is set.
- `scan_id` is set only for sources whose `metadata` is an object, and only when
  `metadata->'inputs'->'image_ids'` meets all of these conditions:
  - it is a non-empty array;
  - every `jsonb_array_elements_text` value matches `^[0-9]+$`;
  - every element matches a `cyl_images` row with a non-NULL `scan_id`;
  - those rows name exactly one distinct scan.

  This is the write-back RPC's own resolution rule.

**Failures leave NULL.** A source that fails the `scan_id` rule SHALL keep a NULL `scan_id`. The
function SHALL report, with `RAISE NOTICE 'cyl recipe backfill: % source(s) with unresolved
image_ids'`, the number of object-`metadata` sources left with a NULL `scan_id` after it runs. It
SHALL NOT raise.

**What it leaves alone.** It SHALL NOT set `argo_workflow_name` or `cyl_pipeline_run_id`, SHALL
NOT modify `metadata`, `name` or `idempotency_key`, and SHALL NOT read `cyl_scan_traits`.

**Access.** It SHALL be owned by `postgres`, with `EXECUTE` revoked from `PUBLIC`, `anon`,
`authenticated` and `service_role`.

#### Scenario: Pipeline sources get a recipe and a scan

- **WHEN** the backfill runs over a pipeline source whose `image_ids` resolve to one scan
- **THEN** its `recipe_key` equals `cyl_trait_recipe_key_v1(metadata)`, its `recipe_key_version`
  is 1, and its `scan_id` is that scan

#### Scenario: Backfilled scan_id agrees with trait rows

- **WHEN** a backfilled pipeline source's `cyl_scan_traits` rows were written by the write-back
  RPC
- **THEN** each of those rows has `scan_id` equal to the source's `scan_id`

#### Scenario: Legacy sources get a pseudo-recipe

- **WHEN** the backfill runs over a source whose `metadata` is NULL, or is a non-object such as
  `'[]'` or JSON `null`
- **THEN** its `recipe_key` is `legacy:<its id>`, its `recipe_key_version` is 1, and its `scan_id`
  is NULL

#### Scenario: Unresolvable image_ids leave scan_id NULL without failing

- **WHEN** the backfill runs over sources whose `image_ids` are missing, not an array, contain
  `"abc"`, match no image, or resolve to two scans
- **THEN** it completes, those sources keep `scan_id` NULL, and its NOTICE reports that number

#### Scenario: Run stamps are not invented

- **WHEN** the backfill runs over any existing source
- **THEN** that source's `argo_workflow_name` and `cyl_pipeline_run_id` remain NULL

#### Scenario: A source written between the two migrations is backfilled

- **WHEN** a source is created by the `20260928130000` RPC body after the recipe-identity migration
  commits and before the write-back migration runs
- **THEN** after the write-back migration, that source has its `recipe_key` and `scan_id` set

#### Scenario: Re-running the backfill changes nothing

- **WHEN** the backfill runs a second time
- **THEN** no row's recipe and run columns change

### Requirement: Recipe-identity migrations are re-runnable and have exact rollbacks

The recipe-identity migration (`*_add_cyl_trait_recipe_key.sql`) and the write-back migration (`*_stamp_cyl_trait_source_recipe_and_run.sql`) SHALL be additive and forward-only, re-runnable as the newest migration, and paired with rollback scripts under `supabase/rollbacks/`.

**The recipe-identity migration** SHALL set `lock_timeout` for its transaction, and SHALL end with
`NOTIFY pgrst, 'reload schema'`.

**The write-back rollback** SHALL restore `insert_cyl_result_envelope(jsonb, text)` to the
`20260928130000` body with the `20260928130100` grants.

**The recipe-identity rollback:**
- SHALL raise without changing anything if the body of any live function other than the three it
  drops still references `recipe_key`, `cyl_pipeline_run_id`, `cyl_trait_recipe_key_v1` or
  `cyl_trait_recipe_payload_v1`;
- otherwise SHALL drop the recipe and run columns with their constraints and indexes, the
  `cyl_pipeline_run_scans_argo_workflow_name_idx` index, the two helpers, and the backfill
  function.

**Types.** The generated `database.types.ts` copies SHALL gain the recipe and run columns.

#### Scenario: Re-applying the migration bodies is idempotent

- **WHEN** each migration's SQL body is executed a second time over seeded data
- **THEN** no error is raised, no backfilled value changes, and exactly one
  `insert_cyl_result_envelope` overload exists, with two arguments

#### Scenario: The write-back rollback restores a9 behavior and grants

- **WHEN** the write-back rollback is applied after the forward migration
- **THEN** a fresh delivery leaves all the recipe and run columns NULL, the cross-Workflow re-delivery
  fallback still reports `status_update_matched = true`, and `EXECUTE` is held exactly as in
  `20260928130100`

#### Scenario: The recipe-identity rollback refuses to run out of order

- **WHEN** the recipe-identity rollback is applied while the stamping body of
  `insert_cyl_result_envelope` is live
- **THEN** it raises, and every column and function remains
