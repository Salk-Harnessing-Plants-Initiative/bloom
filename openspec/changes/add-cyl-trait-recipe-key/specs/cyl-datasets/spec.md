## ADDED Requirements

### Requirement: Datasets freeze trait rows at creation

Bloom SHALL provide `create_cyl_dataset(name text, experiment_id bigint, trait_source_id bigint, qc_set_name json, timepoints json, recipe_key text DEFAULT NULL)`, which inserts one `cyl_datasets` row and freezes the ids of the selected `cyl_scan_traits` rows into `cyl_dataset_traits`, so that a dataset's contents do not change after creation.

**Candidate scans.** Candidates are the scans of `experiment_id` in `cyl_scans_extended`, with two
filters:
- when `timepoints` is non-null, only scans whose `plant_age_days` is in it;
- when `qc_set_name->>'name'` names an existing QC set, plants flagged by that set are excluded. A
  name that matches no set applies no QC filter.

**Selectors.** Exactly one of `trait_source_id` and `recipe_key` SHALL be non-null. Otherwise the
call SHALL raise an error and write nothing.

**Function properties.** It SHALL be `SECURITY INVOKER`, owned by `postgres`, with a
function-level `statement_timeout` of `0`, and SHALL be the only `create_cyl_dataset` overload.
Its `EXECUTE` SHALL be held by exactly `PUBLIC`, `anon`, `authenticated`, `service_role` and its
owner.

#### Scenario: Frozen rows do not change when new sources arrive

- **WHEN** a dataset is created, and a newer source for one of its scans is written afterwards
- **THEN** the dataset's `cyl_dataset_traits` rows are unchanged

#### Scenario: Timepoints and QC filters apply

- **WHEN** a dataset is created with `timepoints = [7]` and a QC set that flags plant P
- **THEN** no frozen row belongs to a scan whose `plant_age_days` is not 7, or to plant P

#### Scenario: An unknown QC set name applies no filter

- **WHEN** a dataset is created with a `qc_set_name` that matches no `cyl_qc_sets` row
- **THEN** it freezes the same rows as with `qc_set_name` NULL

#### Scenario: Zero or two selectors are rejected

- **WHEN** `create_cyl_dataset` is called with both `trait_source_id` and `recipe_key` NULL, or
  both non-null
- **THEN** the call raises an error and no `cyl_datasets` row is created

#### Scenario: bloomctl's five-key named call still resolves

- **WHEN** PostgREST receives `POST /rpc/create_cyl_dataset` naming exactly `name`,
  `experiment_id`, `trait_source_id`, `qc_set_name` and `timepoints`, with `trait_source_id` null
- **THEN** it resolves to the single function with no PGRST202 or PGRST203 error, and returns the
  function's own "exactly one selector" error

#### Scenario: The ACL is exactly the stated grantees

- **WHEN** the function's `aclexplode(proacl)` grantees with `EXECUTE` are listed
- **THEN** they are exactly `PUBLIC`, `anon`, `authenticated`, `service_role` and `postgres`

### Requirement: Source-mode datasets hold one source's rows

When `trait_source_id` is given, `create_cyl_dataset` SHALL freeze only the `cyl_scan_traits` rows whose `source_id` equals it, and SHALL store `trait_source_id` and that source's `recipe_key` on the `cyl_datasets` row.

#### Scenario: A source-mode dataset records its source and recipe

- **WHEN** a dataset is created with `trait_source_id = S`
- **THEN** every frozen row has `source_id = S`, and the dataset row has `trait_source_id = S` and
  a `recipe_key` equal to source `S`'s

### Requirement: Recipe-mode datasets hold one recipe's rows

When `recipe_key` is given, `create_cyl_dataset` SHALL freeze, for each candidate scan that has that recipe, the `cyl_scan_traits` rows of the scan's source of that recipe as defined in the `cyl-trait-read` requirement "Recipe presence is defined by trait rows".

**The dataset row.** It SHALL store `recipe_key` and leave `trait_source_id` NULL.

**Scans without the recipe** contribute no rows.

**Which scans recipe mode can see.** Recipe mode finds each scan's source of the recipe through
the recipe-read scan set (experiments → waves → plants → accessions (inner) → scans), so a
candidate scan whose plant has no accession contributes no rows in recipe mode. Source mode keeps
the full `cyl_scans_extended` candidate set.

**Unknown recipes.** A `recipe_key` that names no stored recipe and is not `'unattributed'` SHALL
raise an error.

#### Scenario: A recipe-mode dataset spans many per-scan sources

- **WHEN** an experiment's 12 scans each have their own source of recipe `K`, and a dataset is
  created with `recipe_key = K`
- **THEN** the dataset freezes rows from all 12 sources, and every frozen row's source has
  `recipe_key = K`

#### Scenario: A recipe-mode dataset matches the recipe read

- **WHEN** a recipe-mode dataset for `K` is created with no timepoint or QC filter
- **THEN** for every scan whose plant has an accession and whose experiment has a species, the
  frozen rows are exactly the `cyl_scan_traits` rows behind
  `get_experiment_traits(experiment_id, recipe_key_ => K)` for that scan

#### Scenario: Other recipes are left out

- **WHEN** a scan's newest source is recipe `K2`, and it also has an older source of recipe `K`
- **THEN** a recipe-mode dataset for `K` freezes that scan's `K` rows and none of its `K2` rows

#### Scenario: A legacy recipe matches source mode

- **WHEN** one dataset is created with `recipe_key = 'legacy:S'` and another with
  `trait_source_id = S`, with the same filters
- **THEN** both freeze the same `cyl_scan_traits` row ids

#### Scenario: A mistyped recipe is rejected

- **WHEN** `create_cyl_dataset` is called with a `recipe_key` that names no stored recipe
- **THEN** the call raises an error and no dataset is created

### Requirement: Datasets record their recipe

`cyl_datasets` SHALL carry a nullable `recipe_key text` column, constrained by the named CHECK `cyl_datasets_recipe_key_format_check` to NULL, 64 lowercase hex characters, `legacy:<integer>` or `'unattributed'`.

**The dataset migration.** The dataset migration (`*_add_cyl_dataset_recipe_mode.sql`) SHALL:
- backfill `recipe_key` from `cyl_trait_sources.recipe_key` for every existing dataset whose
  `trait_source_id` is set;
- add the CHECK in its named, guarded `ADD CONSTRAINT` form;
- use `DROP FUNCTION IF EXISTS` followed by `CREATE OR REPLACE`;
- change no database-level or role-level setting;
- set `lock_timeout`;
- end with `NOTIFY pgrst, 'reload schema'`.

**Its rollback** SHALL restore the `20240904033106` function body with the ACL above, change no
database-level or role-level setting, and drop the column. It SHALL report with `RAISE NOTICE` the number of recipe-mode datasets whose only identity
it drops.

#### Scenario: Existing datasets gain their source's recipe

- **WHEN** the migration runs over datasets built from pipeline source `S`, from legacy source `L`,
  and with a NULL `trait_source_id`
- **THEN** their `recipe_key` becomes `S`'s key, `legacy:L` and NULL respectively, and running the
  backfill again changes nothing

#### Scenario: Re-applying the migration body is idempotent

- **WHEN** the migration's SQL body is executed a second time
- **THEN** no error is raised and exactly one `create_cyl_dataset` overload exists, with six
  arguments

#### Scenario: Rollback restores the five-argument function

- **WHEN** the rollback is applied after the forward migration
- **THEN** only `create_cyl_dataset(text, bigint, bigint, json, json)` exists, with the
  `20240904033106` body and the ACL above, and `cyl_datasets` has no `recipe_key` column
