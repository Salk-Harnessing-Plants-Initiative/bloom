## MODIFIED Requirements

### Requirement: Bulk experiment-scoped trait reads

Bloom SHALL provide `get_experiment_traits(experiment_id_ BIGINT, source_id_ BIGINT DEFAULT NULL, run_id_ TEXT DEFAULT NULL, recipe_key_ TEXT DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL)` returning every trait row for the given experiment, optionally narrowed by `scan_ids_`, in a single call.

**Columns.** It returns `scan_id, date_scanned, plant_age_days, wave_number, plant_id, germ_day,
plant_qr_code, accession_name, trait_name, source_id, trait_value, recipe_key`, in that order.

**Built on the view.** It is built on `cyl_scan_traits_source` and reuses that view's `is_latest`
selection rule rather than re-deriving it.

**Source selection.**

- With `source_id_`, `run_id_` and `recipe_key_` all `NULL`, the function SHALL return the latest
  source per scan for every trait.
- With `source_id_` set, it SHALL return only that source's rows.
- With `run_id_` set, it SHALL return each scan's values from the pipeline run whose
  `pipeline_run_id` equals `run_id_`.
- With `recipe_key_` set, it SHALL return, for each scan, only the rows of that scan's source of
  that recipe, as defined in "Recipe presence is defined by trait rows". Scans without the recipe
  are omitted.
- Supplying more than one of `source_id_`, `run_id_` and `recipe_key_` SHALL raise an error.
- A `recipe_key_` that is not a stored recipe (see "Recipe presence is defined by trait rows")
  and is not `'unattributed'` SHALL raise an error. A stored recipe absent from this experiment
  returns zero rows.

**Narrowing by scan.** `scan_ids_`, when non-null, SHALL restrict every mode to those scans. An
empty array returns zero rows, and ids that are not scans of `experiment_id_` are ignored.

**The `recipe_key` column** carries the row's source's `recipe_key`, or `'unattributed'` when
`source_id` is NULL.

**Row order.** Every mode SHALL order rows by `accessions.name, cyl_plants.id, cyl_scans.id,
trait_name`.

**Parity with the three-argument definition.** With `recipe_key_` and `scan_ids_` both `NULL`, the
first eleven columns and the row order SHALL be identical to those of the `20260728000000`
three-argument definition.

**Parity with `get_scan_traits`.** The latest, `source_id` and `run_id` semantics SHALL match
`get_scan_traits`'s existing behavior byte-for-byte on any `(scan, trait)` combination both
functions can return.

**No cross-source mixing, no dropped values.**

- In the latest, `source_id_` and `recipe_key_` modes, a scan whose selected source did not measure
  a trait that another source measured SHALL NOT have that trait filled in from the other source.
  In `run_id_` mode the source is chosen per `(scan, trait)` (the newest source of that run that
  measured the trait), so a run that delivered a scan more than once can return that scan's traits
  from more than one of its sources.
- A trait whose selected value is non-finite (stored `NULL`) SHALL be returned as a `NULL`-valued
  row, not omitted.

**Experiment scope.** Results SHALL be scoped to `experiment_id_` only. No row from another
experiment SHALL be returned under any argument combination.

#### Scenario: One call returns all traits for an experiment

- **WHEN** `get_experiment_traits(experiment_id_)` is called for an experiment with multiple scans, each
  with multiple measured traits
- **THEN** the call returns every trait for every scan in that experiment in a single response, with no
  `trait_name_` argument required

#### Scenario: Default path matches get_scan_traits' latest semantics

- **WHEN** `get_experiment_traits(experiment_id_)` and `get_scan_traits(experiment_id_, trait_name_)` are
  both called (no source/run arguments) for the same experiment and an overlapping trait name
- **THEN** the two calls agree row-for-row on that trait's `(scan_id, trait_value)` pairs

#### Scenario: Pinning a source matches get_scan_traits byte-for-byte

- **WHEN** `get_experiment_traits(experiment_id_, source_id_=X)` and
  `get_scan_traits(experiment_id_, trait_name_, source_id_=X)` are both called for the same source
- **THEN** the two calls agree row-for-row on that trait's values for source `X`

#### Scenario: Run grouping matches get_scan_traits byte-for-byte

- **WHEN** `get_experiment_traits(experiment_id_, run_id_=R)` and
  `get_scan_traits(experiment_id_, trait_name_, run_id_=R)` are both called for the same run
- **THEN** the two calls agree row-for-row on that trait's values for run `R`, including for a scan
  whose run `R` values were later superseded by a newer run

#### Scenario: Supplying more than one selector is rejected

- **WHEN** `get_experiment_traits` is called with `source_id_` and `run_id_`, with `source_id_` and
  `recipe_key_`, or with `run_id_` and `recipe_key_` non-null
- **THEN** each call raises an error and returns no rows

#### Scenario: No cross-source mixing

- **WHEN** an older source measured traits A and B for a scan and the latest source measured only A
- **THEN** `get_experiment_traits`'s default path returns A from the latest source and does not return B

#### Scenario: No cross-source mixing within a recipe

- **WHEN** a scan has recipe `K1` sources 10 (traits A and B) and 20 (trait A only)
- **THEN** `get_experiment_traits(experiment_id_, recipe_key_ => K1)` returns A from source 20 and
  does not return B

#### Scenario: Non-finite values are surfaced as NULL

- **WHEN** the selected source for a scan stored a `NULL` value for a trait, on the default path or
  in recipe mode
- **THEN** `get_experiment_traits` returns that trait as a row with `trait_value = NULL`, not omitted

#### Scenario: Results never cross experiment boundaries

- **WHEN** `get_experiment_traits` is called for experiment A, with any combination of `source_id_`,
  `run_id_` or `recipe_key_`, including a recipe present only in experiment B, and with `scan_ids_`
  listing experiment B's scans
- **THEN** no row from any other experiment's scans is returned

#### Scenario: An experiment with no trait rows returns cleanly

- **WHEN** `get_experiment_traits` is called for an experiment with no scan-trait rows
- **THEN** the call returns zero rows without error

#### Scenario: A recipe read returns one recipe only

- **WHEN** an experiment's scans carry sources of recipes `K1` and `K2`, and
  `get_experiment_traits(experiment_id_, recipe_key_ => K1)` is called
- **THEN** every returned row has `recipe_key = K1`, and a scan whose only sources are `K2` returns
  no rows

#### Scenario: A recipe read takes each scan's highest source of that recipe

- **WHEN** a scan has two sources of recipe `K1` (ids 10 and 20) and a newer source (id 30) of
  recipe `K2`
- **THEN** `get_experiment_traits(experiment_id_, recipe_key_ => K1)` returns that scan's rows from
  source 20 only

#### Scenario: The unattributed pseudo-recipe reads NULL-source rows

- **WHEN** `get_experiment_traits(experiment_id_, recipe_key_ => 'unattributed')` is called for an
  experiment with `NULL`-source trait rows
- **THEN** exactly those rows are returned, each with `recipe_key = 'unattributed'` and a NULL
  `source_id`

#### Scenario: A legacy pseudo-recipe reads that source's rows

- **WHEN** `get_experiment_traits(experiment_id_, recipe_key_ => 'legacy:L')` is called
- **THEN** it returns the same first eleven columns, in the same order, as
  `get_experiment_traits(experiment_id_, source_id_ => L)`

#### Scenario: A mistyped recipe key is rejected

- **WHEN** `recipe_key_` is 64 hex characters that match no stored `recipe_key`
- **THEN** the call raises an error

#### Scenario: A recipe stored only elsewhere returns nothing

- **WHEN** `recipe_key_` is a stored recipe that no scan of `experiment_id_` has
- **THEN** the call returns zero rows without error

#### Scenario: `scan_ids_` narrows the default path

- **WHEN** `get_experiment_traits(experiment_id_, scan_ids_ => ARRAY[s1])` is called
- **THEN** only scan `s1`'s latest-source rows are returned, and an empty `scan_ids_` returns zero
  rows

#### Scenario: The default path is unaffected by `recipe_key_` and `scan_ids_`

- **WHEN** the `20260728000000` three-argument definition and this definition are each called on
  the same data with the same `experiment_id_`, `source_id_` and `run_id_`
- **THEN** the first eleven columns of every row, and the row order, are identical

### Requirement: Additive, non-destructive bulk-read migration

The migration adding `get_experiment_traits` and `list_experiment_trait_sources` SHALL be additive only — it MUST NOT drop, replace, or alter any existing table, view, or function (including `get_scan_traits`, `cyl_scan_traits_source`, and `cyl_scan_traits_latest`, which it reads but does not modify).

**Its rollback.** A companion manual rollback script SHALL be provided under `supabase/rollbacks/`
that drops both new functions by full argument signature. That script targets the three-argument
`get_experiment_traits`. So on a database where the recipe-read migration has replaced that
function, the recipe-read rollback SHALL be applied first.

**Types.** All five tracked Supabase `database.types.ts` copies SHALL be regenerated to include
both new functions.

#### Scenario: Forward migration adds the bulk-read surface without touching existing objects

- **WHEN** the migration is applied to a database that already has the source-aware read surface
  (`cyl_scan_traits_source`, `cyl_scan_traits_latest`, `get_scan_traits`)
- **THEN** `get_experiment_traits` and `list_experiment_trait_sources` are created and every pre-existing
  view, function, table, and grant is unchanged

#### Scenario: Rollback removes exactly the two new functions

- **WHEN** the companion rollback script is applied to a database where the migration had been
  applied, after the recipe-read rollback if the recipe-read migration had also been applied
- **THEN** `get_experiment_traits` and `list_experiment_trait_sources` no longer exist and every
  pre-existing read object (`get_scan_traits`, `cyl_scan_traits_source`, `cyl_scan_traits_latest`,
  `cyl_scan_trait_names`) is unchanged

## ADDED Requirements

### Requirement: Recipe presence is defined by trait rows

A scan `s` SHALL have recipe `K` exactly when at least one of its `cyl_scan_traits` rows has a `source_id` `c` whose `cyl_trait_sources.recipe_key` is `K` and whose `cyl_trait_sources.scan_id` is `s` or NULL, or has a `NULL` `source_id` when `K` is `'unattributed'`.

**The scan's source of `K`** is the highest such `source_id`, or `NULL` for `'unattributed'`.

**Who uses this definition.** `list_trait_recipes`, `get_trait_recipe_coverage`,
`get_experiment_traits` (with `recipe_key_`) and `create_cyl_dataset` (with `recipe_key`) SHALL
all use it.

**Consequences.**

- A source with no trait rows SHALL contribute no recipe to any scan.
- Rows of a source whose `recipe_key` is NULL SHALL contribute no recipe.
- Rows of a source whose `scan_id` names a different scan SHALL contribute no recipe to the scan
  they sit on. The write-back RPC never writes such rows. A direct `bloom_admin` edit can, and so
  can the backfill, for a source whose image was moved to another scan after its rows were written
  (`authenticated` and `bloom_writer` may UPDATE `cyl_images`).

**Stored recipes.** A _stored recipe_ is a `recipe_key` value present on at least one
`cyl_trait_sources` row, in any experiment.

**Recipe kinds.** A recipe is `'legacy'` when its key starts with `legacy:`, `'unattributed'` for
the key `'unattributed'`, and `'pipeline'` otherwise.

#### Scenario: A source with no trait rows adds no recipe

- **WHEN** a pipeline source of recipe `K3` has `scan_id = s` but no `cyl_scan_traits` rows
- **THEN** scan `s` does not have `K3` in `list_trait_recipes`, in coverage `available_recipes`, or
  in any recipe read

#### Scenario: Rows filed under another scan's source add no recipe

- **WHEN** source `c` has recipe `K` and `scan_id = s1`, and has `cyl_scan_traits` rows on scan `s2`
- **THEN** scan `s2` does not have `K`

#### Scenario: A pipeline source with no scan_id still counts

- **WHEN** source `c` has recipe `K`, `scan_id` NULL, and `cyl_scan_traits` rows on scan `s`
- **THEN** scan `s` has `K`, with `c` as a candidate for its source of `K`

#### Scenario: The four functions agree on a scan's source of a recipe

- **WHEN** coverage reports scan `s` as `included` with `source_id = X` for recipe `K`
- **THEN** `get_experiment_traits(recipe_key_ => K)` returns scan `s`'s rows from source `X`, and a
  recipe-mode dataset for `K` freezes source `X`'s rows for `s`

### Requirement: Recipe listing for a scan selection

Bloom SHALL provide `list_trait_recipes(experiment_ids_ BIGINT[] DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL)` returning one row per recipe that at least one selected scan has.

**The selection.**

- It is the scans reachable from `experiment_ids_` through `get_experiment_traits`' join chain
  (experiments → waves → plants → accessions (inner) → scans).
- When `scan_ids_` is also given, it is intersected with it. When only `scan_ids_` is given, it is
  those ids that are reachable scans.
- Both arguments `NULL` SHALL raise an error.
- An empty array for either argument selects no scans.
- Ids that are not reachable scans are ignored.

**Columns.**

| Column               | Content                                                                                                                                         |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipe_key`         | The recipe                                                                                                                                      |
| `recipe_key_version` | `1` for pipeline and legacy recipes, `NULL` for `unattributed`                                                                                  |
| `recipe_kind`        | `pipeline`, `legacy` or `unattributed`                                                                                                          |
| `definition` (jsonb) | Pipeline: `cyl_trait_recipe_payload_v1` of the recipe's highest source's `metadata`. Legacy: `{source_id, source_name}`. `unattributed`: `NULL` |
| `n_scans`            | The number of selected scans that have the recipe                                                                                               |
| `newest_source_id`   | The highest `source_id` among the selected scans' sources of the recipe. `NULL` for `unattributed`                                              |
| `is_default`         | Whether this is the default recipe                                                                                                              |

**The default recipe.** Exactly one row SHALL have `is_default = true` whenever any row is
returned: the one with the highest `newest_source_id`, with `unattributed` ranking last.

#### Scenario: Recipes in a mixed experiment are listed with counts

- **WHEN** an experiment has 3 scans with only `K1`, 1 scan with only `K2`, 1 scan with both, and
  2 scans with only legacy source `L`
- **THEN** `list_trait_recipes(ARRAY[exp])` returns `K1` with `n_scans` 4, `K2` with 2 and
  `legacy:L` with 2

#### Scenario: The default recipe is the one written most recently

- **WHEN** `K1`'s highest source in the selection is id 50, `K2`'s is id 60, and `K1` covers more
  scans
- **THEN** `K2` has `is_default = true` and `K1` has `false`

#### Scenario: A pipeline definition hashes to its key

- **WHEN** any `pipeline` row is returned
- **THEN** `encode(sha256(convert_to(definition::text, 'UTF8')), 'hex')` equals its `recipe_key`

#### Scenario: A multi-experiment selection counts across experiments

- **WHEN** `list_trait_recipes(ARRAY[e1, e2])` is called and both experiments have scans with `K1`
- **THEN** `K1`'s `n_scans` is the sum over both, and the default is chosen across both

#### Scenario: Both arguments narrow the selection

- **WHEN** `list_trait_recipes(ARRAY[e1], ARRAY[s1, s2])` is called and `s2` belongs to `e2`
- **THEN** only `s1` is counted

#### Scenario: A scan-level selection lists only that scan's recipes

- **WHEN** `list_trait_recipes(scan_ids_ => ARRAY[s])` is called for a scan with `K1` and `K2`
- **THEN** exactly `K1` and `K2` are returned, each with `n_scans` 1

#### Scenario: Legacy-only experiments list their legacy and unattributed recipes

- **WHEN** an experiment's scans have only legacy source `L` rows and `NULL`-source rows
- **THEN** `legacy:L` and `unattributed` are returned, and `legacy:L` is the default

#### Scenario: An unattributed-only selection defaults to unattributed

- **WHEN** every selected scan has only `NULL`-source trait rows
- **THEN** the single row returned is `unattributed`, with `is_default = true`

#### Scenario: Empty selections return no rows

- **WHEN** `list_trait_recipes` is called with an empty array for either argument, or for scans
  with no trait rows
- **THEN** zero rows are returned without error

#### Scenario: A selection argument is required

- **WHEN** `list_trait_recipes()` is called with both arguments `NULL`
- **THEN** the call raises an error

### Requirement: Per-scan recipe coverage

Bloom SHALL provide `get_trait_recipe_coverage(experiment_ids_ BIGINT[] DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL, recipe_key_ TEXT DEFAULT NULL)` returning one row per selected scan, evaluated against one recipe.

**Inputs.**

- The selection is defined as for `list_trait_recipes`.
- The evaluated recipe is `recipe_key_`, or, when that is `NULL`, the default recipe of the same
  selection. That can itself be `NULL` when no selected scan has trait rows.

**Columns:** `scan_id`, `experiment_id`, `plant_qr_code`, `recipe_key` (the evaluated recipe),
`status`, `source_id` and `available_recipes text[]`.

**Status.** `status` SHALL be the first of these that applies:

| Status         | When                                                                                      |
| -------------- | ----------------------------------------------------------------------------------------- |
| `included`     | The scan has the evaluated recipe. `source_id` is its source of that recipe               |
| `no_traits`    | The scan has no trait rows                                                                |
| `legacy_only`  | The scan has at least one recipe, and every recipe it has is `legacy:*` or `unattributed` |
| `other_recipe` | Any other case                                                                            |

**Other rules.**

- `source_id` SHALL be `NULL` for every status other than `included`.
- `available_recipes` SHALL list exactly the recipes the scan has, sorted under `COLLATE "C"`.
- A `recipe_key_` that is not a stored recipe and is not `'unattributed'` SHALL raise an error.

#### Scenario: Coverage reports each scan's status against the default

- **WHEN** coverage is requested for an experiment with scans that have the default `K1`, only
  `K2`, only legacy `L`, and no traits
- **THEN** those scans report `included`, `other_recipe`, `legacy_only` and `no_traits`

#### Scenario: Unattributed-only scans report legacy_only

- **WHEN** the evaluated recipe is `K1` and a scan has only `NULL`-source rows
- **THEN** that scan reports `legacy_only`

#### Scenario: Included scans name the source a recipe read uses

- **WHEN** a scan is `included` for `K1`
- **THEN** its `source_id` equals the `source_id` of every row
  `get_experiment_traits(recipe_key_ => K1)` returns for that scan

#### Scenario: An explicit legacy pick marks pipeline-only scans as other_recipe

- **WHEN** coverage is requested with `recipe_key_ = 'legacy:L'` and a scan has only pipeline
  recipes
- **THEN** that scan reports `other_recipe`

#### Scenario: available_recipes lists everything a scan has

- **WHEN** a scan has sources of `K1` and `K2` and `NULL`-source rows
- **THEN** its `available_recipes` is exactly `K1`, `K2` and `unattributed`

#### Scenario: Rows with no recipe report other_recipe

- **WHEN** a scan's only trait rows belong to a source whose `recipe_key` is NULL
- **THEN** that scan reports `other_recipe` with an empty `available_recipes`

#### Scenario: A selection with no trait data reports no_traits

- **WHEN** coverage is requested with `recipe_key_` `NULL` for scans that have no trait rows
- **THEN** every row reports `no_traits` with `recipe_key` `NULL`

#### Scenario: A mistyped recipe key is rejected

- **WHEN** `recipe_key_` is 64 hex characters that match no stored `recipe_key`
- **THEN** the call raises an error

#### Scenario: A per-scan coverage call works for one scan

- **WHEN** coverage is requested with `scan_ids_ => ARRAY[s]` only, for a reachable scan `s`
- **THEN** exactly one row is returned, for scan `s`

### Requirement: Recipe read functions are not executable by anon

`get_experiment_traits`, `list_trait_recipes` and `get_trait_recipe_coverage` SHALL be `SECURITY INVOKER`, with `EXECUTE` revoked from `PUBLIC` and `anon` and granted to `bloom_agent`, `bloom_user`, `bloom_admin` and `authenticated`.

**Other roles.** `service_role` keeps the `EXECUTE` it holds through Supabase default privileges.
No row-level-security policy or write grant on any table these functions read SHALL be added,
dropped or altered.

#### Scenario: Read roles can call every mode

- **WHEN** a session assumes each of `bloom_agent`, `bloom_user` and `bloom_admin`, and calls all
  three functions in the default mode, with a pipeline `recipe_key_`, with `'legacy:L'`, with
  `'unattributed'` and with `scan_ids_`
- **THEN** every call succeeds, and returns rows whenever the data has rows for that mode

#### Scenario: anon cannot call them

- **WHEN** `has_function_privilege('anon', …, 'EXECUTE')` is checked for each function
- **THEN** it is false

### Requirement: Recipe read RPCs supply every export sidecar field

Every field of export sidecar v1 (`_WIKI/SUPABASE/trait-recipes.export.schema.json`) SHALL either come from a column of `list_trait_recipes`, `get_trait_recipe_coverage` or `get_experiment_traits`, or from a key path under `cyl_trait_sources.metadata` read by an included `source_id`, or be marked exporter-supplied.

**The field table.** `_WIKI/SUPABASE/trait-recipes.md` SHALL name that source for each field.

**What exporters must do.** An _exporter_ (the web download, bloomctl or bloommcp) SHALL build the
sidecar's excluded list from exactly the
coverage rows whose `status` is not `included`, with `reason` equal to `status`.

#### Scenario: Every schema property is mapped

- **WHEN** the schema's properties are compared with the field table in
  `_WIKI/SUPABASE/trait-recipes.md`
- **THEN** every property has a row, and every source named in the table is a column of the named
  RPC's result, a key path under `cyl_trait_sources.metadata`, "exporter-supplied", or, for a
  container, "object" or "array"

#### Scenario: The example sidecar is complete

- **WHEN** `_WIKI/SUPABASE/trait-recipes.export.example.json` is checked against the schema's
  `required` lists at every level
- **THEN** every required property is present

### Requirement: Recipe read migration replaces get_experiment_traits without leaving an overload

The recipe-read migration (`*_add_cyl_trait_recipe_reads.sql`) SHALL `DROP FUNCTION IF EXISTS get_experiment_traits(bigint, bigint, text)` and then `CREATE OR REPLACE` the five-argument function, so that exactly one `get_experiment_traits` overload exists and the migration is re-runnable.

**Schema reload.** It SHALL end with `NOTIFY pgrst, 'reload schema'`.

**Its rollback.** A companion rollback script SHALL drop `list_trait_recipes`,
`get_trait_recipe_coverage` and the five-argument function, and SHALL restore the three-argument
function and grants of `20260728000000`.

**Types.** The generated `database.types.ts` copies SHALL reflect the new signatures.

#### Scenario: bloommcp's three-key named call still resolves

- **WHEN** PostgREST receives `POST /rpc/get_experiment_traits` with body
  `{"experiment_id_": E, "source_id_": null, "run_id_": null}`
- **THEN** it returns HTTP 200 with a JSON array and no PGRST203 (ambiguous overload) error

#### Scenario: The result columns are exactly the twelve, in order

- **WHEN** `get_experiment_traits` is called in any mode
- **THEN** the result's column names are the twelve listed in "Bulk experiment-scoped trait
  reads", in that order

#### Scenario: Re-applying the migration body is idempotent

- **WHEN** the migration's SQL body is executed a second time
- **THEN** no error is raised and exactly one `get_experiment_traits` overload exists, with five
  arguments

#### Scenario: Rollback restores the three-argument function

- **WHEN** the rollback is applied after the forward migration
- **THEN** only `get_experiment_traits(bigint, bigint, text)` exists, with the attributes and
  grants of `20260728000000`, and neither `list_trait_recipes` nor `get_trait_recipe_coverage`
  exists
