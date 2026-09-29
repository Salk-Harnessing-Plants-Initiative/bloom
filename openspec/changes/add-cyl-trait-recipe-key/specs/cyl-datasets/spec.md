## ADDED Requirements

### Requirement: Datasets freeze trait rows at creation

Bloom SHALL provide `create_cyl_dataset(name text, experiment_id bigint, trait_source_id bigint, qc_set_name json, timepoints json, recipe_key text DEFAULT NULL)`, which inserts one `cyl_datasets` row and freezes the selected `cyl_scan_traits` row ids into `cyl_dataset_traits`, so a dataset's contents never change after creation.

**Which rows are frozen.** The rows are those of scans in `experiment_id`, filtered two ways:
- to plants whose `plant_age_days` is in `timepoints`, when `timepoints` is non-null;
- excluding plants flagged by the QC set named in `qc_set_name->>'name'`, when that is non-null.

**Selectors.** Exactly one of `trait_source_id` and `recipe_key` SHALL be non-null; otherwise the
call SHALL raise an error and write nothing.

**Unchanged posture.** The function SHALL remain `SECURITY INVOKER` with a function-level
`statement_timeout` of `0`, and SHALL be the only `create_cyl_dataset` overload.

#### Scenario: Frozen rows do not change when new sources arrive

- **WHEN** a dataset is created, and a newer source for one of its scans is written afterwards
- **THEN** the dataset's `cyl_dataset_traits` rows are unchanged

#### Scenario: Timepoints and QC filters apply

- **WHEN** a dataset is created with `timepoints = [7]` and a QC set that flags plant P
- **THEN** no frozen row belongs to a scan with `plant_age_days` other than 7, or to plant P

#### Scenario: Zero or two selectors are rejected

- **WHEN** `create_cyl_dataset` is called with both `trait_source_id` and `recipe_key` NULL, or
  both non-null
- **THEN** the call raises an error and no `cyl_datasets` row is created

#### Scenario: The existing named-argument call still works

- **WHEN** a PostgREST `POST /rpc/create_cyl_dataset` names exactly `name`, `experiment_id`,
  `trait_source_id`, `qc_set_name` and `timepoints`, as bloomctl does
- **THEN** it resolves to the single function and creates a source-mode dataset

### Requirement: Source-mode datasets hold one source's rows

When `trait_source_id` is given, `create_cyl_dataset` SHALL freeze only `cyl_scan_traits` rows whose `source_id` equals it, and SHALL store `trait_source_id` and that source's `recipe_key` on the `cyl_datasets` row.

#### Scenario: A source-mode dataset records its source and recipe

- **WHEN** a dataset is created with `trait_source_id = S`
- **THEN** every frozen row has `source_id = S`, and the dataset row has `trait_source_id = S` and
  `recipe_key` equal to source S's `recipe_key`

### Requirement: Recipe-mode datasets hold one recipe's rows

When `recipe_key` is given, `create_cyl_dataset` SHALL freeze, for each matching scan, the `cyl_scan_traits` rows of that scan's highest `source_id` whose `cyl_trait_sources.recipe_key` equals it.

**The pseudo-recipe.** For `'unattributed'`, it SHALL freeze the scan's `NULL`-source rows
instead.

**What the dataset row records.** It SHALL store `recipe_key` and leave `trait_source_id` NULL.

**Scans without the recipe.** A scan with no rows of that recipe contributes no rows.

**Unknown recipes.** A `recipe_key` that matches no stored recipe and is not `'unattributed'`
SHALL raise an error.

#### Scenario: A recipe-mode dataset spans many per-scan sources

- **WHEN** an experiment's 12 scans each have their own source of recipe K, and a dataset is
  created with `recipe_key = K`
- **THEN** the dataset freezes rows from all 12 sources, and every frozen row's source has
  `recipe_key = K`

#### Scenario: A recipe-mode dataset matches the recipe read

- **WHEN** a recipe-mode dataset for K is created with no timepoint or QC filter
- **THEN** for every scan whose plant has an accession, its frozen rows are exactly the
  `cyl_scan_traits` rows behind `get_experiment_traits(experiment_id, recipe_key_ => K)` for that
  scan (`get_experiment_traits` omits plants with no accession; the dataset's scan set is
  unchanged from today's `cyl_scans_extended` filter)

#### Scenario: Other recipes are left out

- **WHEN** a scan's newest source is recipe K2 and it also has an older source of recipe K
- **THEN** a recipe-mode dataset for K freezes that scan's older K rows, and none of its K2 rows

#### Scenario: A mistyped recipe is rejected

- **WHEN** `create_cyl_dataset` is called with a `recipe_key` that matches no stored recipe
- **THEN** the call raises an error and no dataset is created

### Requirement: Datasets record their recipe

`cyl_datasets` SHALL carry a nullable `recipe_key text` column.

**Backfill.** The migration adding it SHALL backfill it from `cyl_trait_sources.recipe_key` for
every existing dataset whose `trait_source_id` is set.

**Rollback.** A companion rollback SHALL drop the column and restore the previous five-argument
`create_cyl_dataset`.

#### Scenario: Existing datasets gain their source's recipe

- **WHEN** the migration runs over an existing dataset built from source S
- **THEN** the dataset's `recipe_key` equals source S's `recipe_key`

#### Scenario: Rollback restores the five-argument function

- **WHEN** the rollback is applied after the forward migration
- **THEN** only `create_cyl_dataset(text, bigint, bigint, json, json)` exists, with its previous
  body, and `cyl_datasets` has no `recipe_key` column
