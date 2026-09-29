## MODIFIED Requirements

### Requirement: Bulk experiment-scoped trait reads

Bloom SHALL provide `get_experiment_traits(experiment_id_ BIGINT, source_id_ BIGINT DEFAULT NULL, run_id_ TEXT DEFAULT NULL, recipe_key_ TEXT DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL)` returning every trait row for the given experiment in a single call.

**Columns and basis.** It returns `scan_id, date_scanned, plant_age_days, wave_number, plant_id,
germ_day, plant_qr_code, accession_name, trait_name, source_id, trait_value, recipe_key`. It is
built on `cyl_scan_traits_source` and reuses that view's `is_latest` selection rule rather than
re-deriving it.

**Source selection.**
- With `source_id_`, `run_id_` and `recipe_key_` all `NULL`, the function SHALL return the
  latest source per scan for every trait.
- With `source_id_` set it SHALL return only that source's rows.
- With `run_id_` set it SHALL return each scan's values from the pipeline run whose
  `pipeline_run_id` equals `run_id_`.
- With `recipe_key_` set it SHALL return, for each scan, the rows of that scan's highest
  `source_id` whose `cyl_trait_sources.recipe_key` equals `recipe_key_`. For
  `recipe_key_ = 'unattributed'`, it SHALL return that scan's `NULL`-source rows instead. Scans
  with no such rows are omitted.
- Supplying more than one of `source_id_`, `run_id_` and `recipe_key_` SHALL raise an error.

**Narrowing.** `scan_ids_`, when non-null, SHALL restrict every mode to those scans. An empty
array returns zero rows.

**The `recipe_key` column** SHALL carry the row's source's `recipe_key`, or `'unattributed'` for
a `NULL` `source_id`.

**Parity.** With `recipe_key_` and `scan_ids_` both `NULL`, results SHALL be identical to the
previous three-argument function's. The latest, `source_id` and `run_id` semantics SHALL match
`get_scan_traits`'s existing behavior byte-for-byte on any `(scan, trait)` combination both
functions can return.

**No mixing, and no dropped values.**
- A scan whose latest source did not measure a trait an older source measured SHALL NOT have
  that trait backfilled from the older source (no cross-source mixing).
- A trait whose selected value is non-finite (stored `NULL`) SHALL be returned as a
  `NULL`-valued row, not omitted.

**Scope.** Results SHALL be scoped to `experiment_id_` only. No row from another experiment
SHALL be returned under any argument combination, including a `scan_ids_` that names another
experiment's scans.

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

- **WHEN** `get_experiment_traits` is called with any two of `source_id_`, `run_id_` and
  `recipe_key_` non-null
- **THEN** the call raises an error and returns no rows

#### Scenario: No cross-source mixing

- **WHEN** an older source measured traits A and B for a scan and the latest source measured only A
- **THEN** `get_experiment_traits`'s default path returns A from the latest source and does not return B

#### Scenario: Non-finite values are surfaced as NULL

- **WHEN** the latest source for a scan stored a `NULL` value for a trait
- **THEN** `get_experiment_traits` returns that trait as a row with `trait_value = NULL`, not omitted

#### Scenario: Results never cross experiment boundaries

- **WHEN** `get_experiment_traits` is called for experiment A, with or without `source_id_`,
  `run_id_`, `recipe_key_` or `scan_ids_` set, including a `scan_ids_` that lists experiment B's
  scans
- **THEN** no row from any other experiment's scans is returned

#### Scenario: An experiment with no trait rows returns cleanly

- **WHEN** `get_experiment_traits` is called for an experiment with no scan-trait rows
- **THEN** the call returns zero rows without error

#### Scenario: A recipe read returns one recipe only

- **WHEN** an experiment's scans carry sources of recipes K1 and K2, and
  `get_experiment_traits(experiment_id_, recipe_key_ = K1)` is called
- **THEN** every returned row has `recipe_key = K1`, and a scan whose only sources are K2 returns
  no rows

#### Scenario: A recipe read takes each scan's newest source of that recipe

- **WHEN** a scan has two sources of recipe K1 (ids 10 and 20) and a newer source (id 30) of
  recipe K2
- **THEN** `get_experiment_traits(experiment_id_, recipe_key_ = K1)` returns that scan's rows
  from source 20 only

#### Scenario: The unattributed pseudo-recipe reads NULL-source rows

- **WHEN** `get_experiment_traits(experiment_id_, recipe_key_ = 'unattributed')` is called for an
  experiment with `NULL`-source trait rows
- **THEN** exactly those rows are returned, each with `recipe_key = 'unattributed'` and
  `source_id` NULL

#### Scenario: scan_ids_ narrows the default path

- **WHEN** `get_experiment_traits(experiment_id_, scan_ids_ => ARRAY[s1])` is called
- **THEN** only scan `s1`'s latest-source rows are returned

#### Scenario: The default path is unchanged by the new arguments

- **WHEN** `get_experiment_traits(experiment_id_)` is called after this change, on the same data
  as before it
- **THEN** the first eleven columns of every row are identical to the previous function's
  output, in the same order

## ADDED Requirements

### Requirement: Recipe listing for a scan selection

Bloom SHALL provide `list_trait_recipes(experiment_ids_ BIGINT[] DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL)` returning one row per recipe present among the selected scans' trait data.

**The selection.**
- It is the scans of `experiment_ids_`, intersected with `scan_ids_` when both are given, or
  `scan_ids_` alone.
- It is reached through the same experiments → waves → plants → accessions (inner) → scans chain
  that `get_experiment_traits` uses.
- Calling with both arguments `NULL` SHALL raise an error.

**Columns:**

| Column | Content |
|---|---|
| `recipe_key` | The key |
| `recipe_key_version` | The key version |
| `recipe_kind` | `pipeline`, `legacy` or `unattributed` |
| `definition` | Pipeline recipes: the recipe-key payload (models, `predict_code_sha`, `traits_code_sha`, `predict_output_params`), taken from the newest source's `metadata`. Legacy: `{source_id, source_name}`. `unattributed`: `NULL` |
| `n_scans` | Selected scans having that recipe |
| `newest_source_id` | The recipe's highest `source_id` among the selected scans (`NULL` for `unattributed`) |
| `is_default` | Whether this recipe is the default |

**The default.** Exactly one row SHALL have `is_default` true when any row is returned: the
recipe with the highest `newest_source_id`. `unattributed` ranks below every recipe that has a
source.

**Legacy data.**
- A scan has a legacy recipe when it has at least one `cyl_scan_traits` row of that legacy
  source.
- It has `unattributed` when it has at least one `NULL`-source row.

**Implementation constraint.** Both checks SHALL be per-scan index probes (`LATERAL … LIMIT 1`),
not a scan of `cyl_scan_traits`.

#### Scenario: Recipes in a mixed experiment are listed with counts

- **WHEN** an experiment has 3 scans under recipe K1, 1 scan under K2, and 2 scans with only
  legacy source L
- **THEN** `list_trait_recipes(ARRAY[exp])` returns K1 (`n_scans` 3), K2 (1) and `legacy:L` (2)

#### Scenario: Newest is the recipe written most recently

- **WHEN** K1's highest source in the selection is id 50, K2's is id 60, and K1 covers more scans
- **THEN** K2 has `is_default` true and K1 false

#### Scenario: A scan-level selection lists only that scan's recipes

- **WHEN** `list_trait_recipes(scan_ids_ => ARRAY[s])` is called for a scan with sources of K1
  and K2
- **THEN** exactly K1 and K2 are returned, each with `n_scans` 1

#### Scenario: Legacy-only experiments list their legacy and unattributed recipes

- **WHEN** an experiment's scans have only legacy source L rows and `NULL`-source rows
- **THEN** `legacy:L` and `unattributed` are returned, and `legacy:L` is the default

#### Scenario: An empty selection returns no rows

- **WHEN** the selected scans have no trait rows
- **THEN** zero rows are returned without error

#### Scenario: A selection argument is required

- **WHEN** `list_trait_recipes()` is called with both arguments `NULL`
- **THEN** the call raises an error

### Requirement: Per-scan recipe coverage

Bloom SHALL provide `get_trait_recipe_coverage(experiment_ids_ BIGINT[] DEFAULT NULL, scan_ids_ BIGINT[] DEFAULT NULL, recipe_key_ TEXT DEFAULT NULL)` returning one row per selected scan, for one evaluated recipe.

**The selection and the evaluated recipe.**
- The selection is defined as for `list_trait_recipes`.
- The evaluated recipe is `recipe_key_`, or, when that is `NULL`, the `list_trait_recipes`
  default for the same selection.

**Columns:** `scan_id`, `experiment_id`, `plant_qr_code`, `recipe_key` (the evaluated recipe),
`status`, `source_id` and `available_recipes text[]`.

**Status.** Each row's `status` SHALL be the first that applies:

| Status | When |
|---|---|
| `included` | The scan has trait rows of the evaluated recipe. `source_id` is the source `get_experiment_traits(recipe_key_ => …)` would read for it |
| `no_traits` | The scan has no trait rows |
| `legacy_only` | All of the scan's recipes are `legacy:*` or `unattributed` |
| `other_recipe` | Any other case |

**`source_id`** SHALL be `NULL` for every status except `included`.

**Unknown recipes.** A `recipe_key_` that matches no `cyl_trait_sources.recipe_key` and is not
`'unattributed'` SHALL raise an error.

#### Scenario: Coverage reports each scan's status against the default

- **WHEN** coverage is requested for an experiment with scans under K1 (default), under K2 only,
  under legacy L only, and with no traits
- **THEN** those scans report `included`, `other_recipe`, `legacy_only` and `no_traits`
  respectively

#### Scenario: Included scans name the source a recipe read would use

- **WHEN** a scan is `included` for K1
- **THEN** its `source_id` equals the `source_id` that `get_experiment_traits(recipe_key_ => K1)`
  returns for that scan

#### Scenario: An explicit legacy pick marks pipeline-only scans as other_recipe

- **WHEN** coverage is requested with `recipe_key_ = 'legacy:L'` and a scan has only pipeline
  recipes
- **THEN** that scan reports `other_recipe`

#### Scenario: available_recipes lists everything a scan has

- **WHEN** a scan has sources of K1 and K2 and `NULL`-source rows
- **THEN** its `available_recipes` contains exactly K1, K2 and `unattributed`

#### Scenario: A mistyped recipe key is rejected

- **WHEN** `recipe_key_` names no stored recipe and is not `'unattributed'`
- **THEN** the call raises an error

#### Scenario: A per-scan coverage call works for one scan

- **WHEN** coverage is requested with `scan_ids_ => ARRAY[s]` only
- **THEN** exactly one row is returned for scan `s`

### Requirement: Recipe read functions grant EXECUTE to the read roles only

`get_experiment_traits`, `list_trait_recipes` and `get_trait_recipe_coverage` SHALL be `SECURITY INVOKER` with `EXECUTE` revoked from `PUBLIC` and `anon` and granted to exactly `bloom_agent`, `bloom_user`, `bloom_admin` and `authenticated`.

**Nothing else changes.** This change SHALL NOT add, drop or alter any row-level-security policy
or write grant on any table these functions read.

#### Scenario: Read roles can call all three functions

- **WHEN** a session assumes each of `bloom_agent`, `bloom_user`, `bloom_admin` and
  `authenticated` and calls each function
- **THEN** every call is permitted

#### Scenario: anon cannot call them

- **WHEN** `has_function_privilege('anon', …, 'EXECUTE')` is checked for each function
- **THEN** it is false

### Requirement: Recipe read RPCs supply every export sidecar field

Every field of the export sidecar v1 defined in `_WIKI/SUPABASE/trait-recipes.md` that describes the recipe, the included scans or the excluded scans SHALL be derivable from columns returned by `list_trait_recipes`, `get_trait_recipe_coverage` and `get_experiment_traits` alone.

**Fields that describe a recipe.** These SHALL come from:
- `definition` for the keyed fields;
- the `metadata` of the included sources, reached by `source_id`, for the observed-but-unkeyed
  fields.

**The docs page** SHALL name, for each sidecar field, the RPC column it comes from.

#### Scenario: The excluded list is the non-included coverage rows

- **WHEN** a producer builds a sidecar for recipe K over a selection
- **THEN** its `excluded` entries are exactly the `get_trait_recipe_coverage` rows for K whose
  `status` is not `included`, with `reason = status` and the same `available_recipes`

#### Scenario: The docs page maps every field

- **WHEN** the sidecar field table in `_WIKI/SUPABASE/trait-recipes.md` is read
- **THEN** each field lists a source column of one of the three RPCs, or is a producer-supplied
  field (`generated_at`, `generated_by`, `selection`)

### Requirement: Recipe read migration replaces get_experiment_traits without leaving an overload

The migration SHALL `DROP FUNCTION get_experiment_traits(bigint, bigint, text)` before creating the five-argument function, so exactly one `get_experiment_traits` overload exists afterwards.

**Rollback.** A companion rollback script SHALL drop `list_trait_recipes`,
`get_trait_recipe_coverage` and the five-argument function, and restore the three-argument
function exactly as defined in `20260728000000`, grants included.

**Types.** The generated `database.types.ts` copies SHALL reflect the new signatures.

#### Scenario: A three-key named call still resolves

- **WHEN** PostgREST receives `POST /rpc/get_experiment_traits` with body
  `{"experiment_id_": E, "source_id_": null, "run_id_": null}`
- **THEN** it resolves to the single five-argument function without an ambiguity error

#### Scenario: Rollback restores the three-argument function

- **WHEN** the rollback is applied after the forward migration
- **THEN** only `get_experiment_traits(bigint, bigint, text)` exists, with its original body and
  grants, and the two new functions do not exist
