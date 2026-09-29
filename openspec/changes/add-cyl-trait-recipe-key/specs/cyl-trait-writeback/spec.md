## ADDED Requirements

### Requirement: Trait source recipe identity columns

`cyl_trait_sources` SHALL carry `recipe_key text`, `recipe_key_version smallint`, `scan_id bigint REFERENCES cyl_scans(id)`, `argo_workflow_name text` and `cyl_pipeline_run_id bigint REFERENCES cyl_pipeline_runs(id)`, all nullable.

**The key.** `recipe_key` identifies how a source's values were computed, independent of which
scan they belong to.

- **Pipeline sources.** For a source with `metadata` (a contracts Provenance), `recipe_key` SHALL
  be `cyl_trait_recipe_key_v1(metadata)`. That is the lowercase hex sha256 of the UTF-8 text of a
  jsonb object with these keys:
  - `models`: the `[registry_id, version, weights_checksum]` triple of each
    `metadata->'predict_models'` entry, ordered by each triple's `jsonb::text`, with duplicates
    kept;
  - `predict_code_sha`;
  - `traits_code_sha`;
  - `predict_output_params`, present only when that value is a non-empty object.
- **Excluded fields.** The key SHALL NOT depend on `scan_key`, `inputs.images_checksum`,
  `params.param_hash`, `contract_version`, `root_type`, `sleap_nn_version`, container digests or
  `predict_inference_config`.
- **Legacy sources.** For a source whose `metadata` is NULL, `recipe_key` SHALL be
  `'legacy:' || id`.
- **Version.** `recipe_key_version` SHALL be `1` for both forms.

**Constraints.**
- A CHECK SHALL restrict `recipe_key` to NULL, `^[0-9a-f]{64}$` or `^legacy:[0-9]+$`.
- A CHECK SHALL restrict `recipe_key_version` to NULL or `1`.
- `cyl_trait_sources(recipe_key)` and `cyl_trait_sources(scan_id)` SHALL be indexed, and so
  SHALL `cyl_pipeline_run_scans(argo_workflow_name)`.

**The helper.** `cyl_trait_recipe_key_v1(jsonb)` SHALL be `IMMUTABLE`, return NULL for NULL
input, and have `EXECUTE` revoked from `PUBLIC, anon, authenticated`.

**Grants on the new columns.** None of the new columns SHALL be granted to `bloom_workflows`,
whose column-scoped `SELECT` on this table stays exactly `(id, metadata, idempotency_key)`.

#### Scenario: The key ignores per-scan inputs

- **WHEN** two Provenance objects differ only in `scan_key`, `inputs`, `params`,
  `contract_version`, container digests or `predict_inference_config`
- **THEN** `cyl_trait_recipe_key_v1` returns the same value for both

#### Scenario: The key changes with any computation input

- **WHEN** two Provenance objects differ in any model triple, `predict_code_sha`,
  `traits_code_sha` or a non-empty `predict_output_params`
- **THEN** `cyl_trait_recipe_key_v1` returns different values

#### Scenario: Model order and empty output params do not matter

- **WHEN** two Provenance objects list the same models in a different order, or one has
  `predict_output_params` null, one `{}`, and one lacks the key
- **THEN** each group yields a single `cyl_trait_recipe_key_v1` value

#### Scenario: root_type is not part of the key

- **WHEN** two Provenance objects have identical model triples but different `root_type` values
- **THEN** `cyl_trait_recipe_key_v1` returns the same value for both

#### Scenario: A malformed recipe_key is rejected

- **WHEN** a row is written with a `recipe_key` that is neither 64 lowercase hex characters nor
  `legacy:<integer>`
- **THEN** the write is rejected by the CHECK constraint

#### Scenario: bloom_workflows cannot read the new columns

- **WHEN** a session assumes `bloom_workflows` and selects `recipe_key`, `scan_id`,
  `argo_workflow_name` or `cyl_pipeline_run_id` from `cyl_trait_sources`
- **THEN** the read is denied

### Requirement: Write-back stamps each new source with its recipe, scan, Workflow and run

On a non-no-op delivery, `insert_cyl_result_envelope(jsonb, text)` SHALL set the five columns in the same transaction as the source insert.

**What is stored:**
- `recipe_key` = `cyl_trait_recipe_key_v1(provenance)`, and `recipe_key_version` = 1;
- `scan_id` = the scan it resolved from `provenance.inputs.image_ids`;
- `argo_workflow_name` = `p_argo_workflow_name`;
- `cyl_pipeline_run_id` = the single distinct `run_id` among `cyl_pipeline_run_scans` rows whose
  `argo_workflow_name` equals `p_argo_workflow_name`.

**When the run stays NULL.** `cyl_pipeline_run_id` SHALL be NULL when `p_argo_workflow_name` is
NULL, when no such row exists, or when more than one distinct `run_id` matches. The lookup SHALL
NOT require a row for the resolved scan.

**What does not change.** On a no-op re-delivery the RPC SHALL NOT change any of the five
columns, whatever `p_argo_workflow_name` it receives. Its return value, its per-scan status
updates (including their fallback), its validation and its grants SHALL be unchanged from
"Write-back RPC ingests a ResultEnvelope". It SHALL keep the `(jsonb, text)` signature, as the
only overload.

#### Scenario: A fresh delivery records its recipe and scan

- **WHEN** a valid envelope is ingested for the first time
- **THEN** its new `cyl_trait_sources` row has `recipe_key` equal to
  `cyl_trait_recipe_key_v1(metadata)`, `recipe_key_version = 1`, and `scan_id` equal to the
  returned `scan_id`

#### Scenario: A Bloom-dispatched delivery records its Workflow and run

- **WHEN** an envelope is ingested with `p_argo_workflow_name = W`, and W appears on
  `cyl_pipeline_run_scans` rows of exactly one run R
- **THEN** the new source has `argo_workflow_name = W` and `cyl_pipeline_run_id = R`

#### Scenario: An unrequested scan still records its run

- **WHEN** an envelope for scan S is ingested under Workflow W, W belongs to run R, and R has
  no run-scan row for S
- **THEN** the new source has `cyl_pipeline_run_id = R`, no run-scan row is inserted, and
  `status_update_matched` is `false` as before

#### Scenario: A hand-submitted delivery records only its Workflow

- **WHEN** an envelope is ingested with `p_argo_workflow_name = W` and no `cyl_pipeline_run_scans`
  row carries W
- **THEN** the new source has `argo_workflow_name = W` and `cyl_pipeline_run_id` NULL

#### Scenario: An ambiguous Workflow name records no run

- **WHEN** `cyl_pipeline_run_scans` rows of two different runs both carry the ingesting
  `p_argo_workflow_name`
- **THEN** the new source has `cyl_pipeline_run_id` NULL

#### Scenario: A delivery with no Workflow name records neither

- **WHEN** an envelope is ingested with `p_argo_workflow_name` NULL
- **THEN** the new source has `argo_workflow_name` and `cyl_pipeline_run_id` both NULL, and
  `recipe_key` and `scan_id` still set

#### Scenario: A no-op re-delivery leaves the stamps alone

- **WHEN** an already-ingested envelope is delivered again under a different
  `p_argo_workflow_name`
- **THEN** the existing source's `recipe_key`, `recipe_key_version`, `scan_id`,
  `argo_workflow_name` and `cyl_pipeline_run_id` are unchanged

#### Scenario: A failed delivery leaves no stamped source

- **WHEN** a delivery fails validation after the source insert, for example on an unresolvable
  `image_ids` or a non-scan-grain trait
- **THEN** no `cyl_trait_sources` row exists for its `idempotency_key`

### Requirement: Existing trait sources are backfilled with recipe identity

The migration adding the recipe identity columns SHALL backfill `recipe_key` and `recipe_key_version` on every existing `cyl_trait_sources` row, and `scan_id` on every pipeline source whose `image_ids` resolve to exactly one scan.

**What the backfill writes:**
- `recipe_key` SHALL be computed by the same `cyl_trait_recipe_key_v1` the RPC uses, or
  `legacy:<id>` for `metadata IS NULL`.
- `scan_id` SHALL be resolved by the RPC's own rule: the distinct non-null `cyl_images.scan_id`
  of `metadata->'inputs'->'image_ids'`. A source with any other result stays NULL, and the
  migration SHALL report how many.
- Legacy sources SHALL keep `scan_id` NULL.

**What it does not touch.** The backfill SHALL NOT set `argo_workflow_name` or
`cyl_pipeline_run_id`, because no record of which Workflow wrote an existing source exists. It
SHALL NOT modify `metadata`, `name` or `idempotency_key`.

**Re-running.** Re-applying the migration SHALL be a no-op.

#### Scenario: Pipeline sources get a recipe and a scan

- **WHEN** the migration runs over a pipeline source whose `image_ids` resolve to one scan
- **THEN** its `recipe_key` equals `cyl_trait_recipe_key_v1(metadata)` and its `scan_id` is that
  scan

#### Scenario: Backfilled scan_id agrees with trait rows

- **WHEN** a backfilled pipeline source has `cyl_scan_traits` rows
- **THEN** every one of those rows has `scan_id` equal to the source's backfilled `scan_id`

#### Scenario: Legacy sources get a pseudo-recipe

- **WHEN** the migration runs over a source with `metadata` NULL
- **THEN** its `recipe_key` is `legacy:<its id>`, its `recipe_key_version` is 1 and its `scan_id`
  is NULL

#### Scenario: Run stamps are not invented

- **WHEN** the migration runs over any existing source
- **THEN** its `argo_workflow_name` and `cyl_pipeline_run_id` remain NULL

### Requirement: Additive recipe-identity migration with a companion rollback

The recipe-identity schema migration and the write-back RPC migration SHALL be additive and forward-only, each with a companion rollback script under `supabase/rollbacks/`.

**Additive only.** The migrations SHALL NOT drop or rewrite any existing column, table or data
beyond the backfill.

**The rollbacks.**
- The RPC rollback SHALL restore the `20260928130000` body of
  `insert_cyl_result_envelope(jsonb, text)` exactly, grants included.
- The schema rollback SHALL drop the five columns, their constraints and indexes, and the helper.

**Types.** The generated `database.types.ts` copies SHALL gain the five columns.

#### Scenario: Rollback restores the a9 write-back body

- **WHEN** the RPC rollback is applied after the forward migration
- **THEN** `insert_cyl_result_envelope(jsonb, text)`'s body equals the `20260928130000`
  definition, and its `EXECUTE` grants are unchanged

#### Scenario: Re-applying the migrations is idempotent

- **WHEN** both forward migrations are applied a second time
- **THEN** no error is raised, no backfilled value changes, and exactly one
  `insert_cyl_result_envelope` overload exists
