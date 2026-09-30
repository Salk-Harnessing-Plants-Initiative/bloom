# Trait recipes and exports

A **recipe** is how a set of cyl trait values was computed: which models, which predict and
traits code, and which output-defining predict parameters. Pipeline write-back stores one
`cyl_trait_sources` row per scan, so an experiment's latest values can mix recipes. The recipe
key lets a reader take one recipe across a whole selection, and say which scans that leaves out.

The rules live in the specs; this page explains them and defines the export sidecar.

- Key definition: `openspec/specs/cyl-trait-writeback/spec.md`, "Recipe key v1 definition".
- Presence, listing, coverage and the recipe read: `openspec/specs/cyl-trait-read/spec.md`
  ("Recipe presence is defined by trait rows", "Recipe listing for a scan selection", "Per-scan
  recipe coverage", "Bulk experiment-scoped trait reads").
- Datasets: `openspec/specs/cyl-datasets/spec.md`.
- Design rationale: the `add-cyl-trait-recipe-key` change (bloom#935, bloom#937).

## Recipe, idempotency key and source

|                                                           | Identifies                                                                                                         | Per scan?                                      |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| **Idempotency key** (`cyl_trait_sources.idempotency_key`) | One job: this computation on this scan's images and params. Write-back uses it to make re-delivery a no-op         | Yes                                            |
| **Recipe key** (`cyl_trait_sources.recipe_key`)           | The models, code and output params: the idempotency payload without `scan_key`, `images_checksum` and `param_hash` | No: shared by every scan computed the same way |
| **Source** (`cyl_trait_sources.id`)                       | One delivery's rows. A pipeline source covers one scan; a legacy source covers many                                | Pipeline: yes                                  |

`list_experiment_trait_sources` lists sources. For pipeline data that is one per scan, so pinning
one source reads one scan. Use `list_trait_recipes` and `recipe_key_` to read a coherent set.

**Pseudo-recipes.**

- `legacy:<source_id>`: a legacy source with no provenance.
- `unattributed`: trait rows with no source. Never stored on `cyl_trait_sources`; a recipe-mode
  dataset may record it.

**Not in the key.** These are recorded but do not split a recipe:

- `contract_version`, `sleap_nn_version` and `traits_sleap_roots_version`;
- the container digests;
- `predict_inference_config` (device, batch size);
- the run and orchestration fields.

An age-window model switch inside one experiment is a different recipe.

**Known blind spot.** The traits step picks a sleap-roots Pipeline class by species, mode and
age window, and provenance does not record which. The recipe key does not include age, so it
sees that choice only through `traits_code_sha`, and through the models when an age window also
switches them (talmolab/sleap-roots-contracts#45).

**Provenance without the keyed fields.** A source whose provenance is a JSON object with no
`predict_models` and no code shas gets the key of the empty payload, and every such source shares
it, labelled `pipeline`. Deliveries through bloomctl cannot produce one: contracts requires those
fields. A hand-built envelope can, because the write-back RPC does not check them. The
pre-promotion dry run (`tests/integration/fixtures/recipe_backfill_dry_run.sql`) reports these as
`empty_payload`, expected 0.

## Latest versus default recipe

- **Latest** is `get_experiment_traits`' unpinned read: each scan's highest `source_id`. It is
  unchanged by recipes and can mix recipes across scans.
- **Default recipe** is the one `list_trait_recipes` proposes for a whole selection: the recipe
  written most recently (highest `source_id`). `n_scans` shows coverage, so a caller can choose
  by coverage instead. `unattributed` ranks last.

## Reading one recipe

```sql
-- The recipes an experiment has, with scan counts and the default.
SELECT * FROM list_trait_recipes(ARRAY[12880747]);

-- Which scans a recipe covers, and why the others are left out.
SELECT * FROM get_trait_recipe_coverage(ARRAY[12880747], NULL, '<recipe_key>');

-- That recipe's traits: each scan's highest source of it.
SELECT * FROM get_experiment_traits(12880747, recipe_key_ => '<recipe_key>');
```

- **Coverage statuses:** `included`, `other_recipe`, `legacy_only` (which covers
  unattributed-only scans too) and `no_traits`.
- **Selections** are experiments, scans, or both (intersected). A multi-experiment export makes
  one `get_experiment_traits` call per experiment, with the same `recipe_key_`.
- **Performance boundary:** a recipe read of a large legacy source (experiment 1's `legacy:5` is
  about 13.9M rows) still exceeds PostgREST's 8 s limit; that is bloom#936.

**Datasets.** `create_cyl_dataset` takes exactly one of `trait_source_id` and `recipe_key`. A
PostgREST recipe-mode call still sends every argument:

```json
{
  "name": "canola-a9",
  "experiment_id": 12880747,
  "trait_source_id": null,
  "qc_set_name": null,
  "timepoints": null,
  "recipe_key": "<recipe_key>"
}
```

## Export sidecar v1

An export writes three files:

| File                  | Content                                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `<stem>.csv`          | One header row, no `#` lines, one row per scan. Metadata columns, then `recipe_key` and `source_id`, then the traits |
| `<stem>.export.json`  | The sidecar: the recipe, what was included, and every excluded scan with its reason. Authoritative                   |
| `<stem>.excluded.csv` | Optional flat copy of the excluded list                                                                              |

- **The format.** The schema is [`trait-recipes.export.schema.json`](trait-recipes.export.schema.json),
  and there is an illustrative example in [`trait-recipes.export.example.json`](trait-recipes.export.example.json).
  Its keys and ids are made up.
- **The stem** is `<experiment-slug>_<recipe_key[:8]>_<yyyymmdd>`.
- **An exporter** (the web download, bloomctl or bloommcp) builds `excluded` from exactly the
  coverage rows whose status is not `included`, with `reason` equal to the status.

**Where each field comes from.** `tests/unit/test_trait_recipe_export_schema.py` checks this
table against the schema and the RPCs.

| Field                                         | Source                                                             | Notes                                     |
| --------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------- |
| `export_schema_version`                       | exporter-supplied                                                  | Always `1`                                |
| `generated_at`                                | exporter-supplied                                                  |                                           |
| `generated_by`                                | object                                                             |                                           |
| `generated_by.tool`                           | exporter-supplied                                                  |                                           |
| `generated_by.version`                        | exporter-supplied                                                  |                                           |
| `selection`                                   | object                                                             |                                           |
| `selection.experiment_ids`                    | exporter-supplied                                                  | The `experiment_ids_` passed              |
| `selection.scan_ids`                          | exporter-supplied                                                  | The `scan_ids_` passed, or null           |
| `selection.filters`                           | exporter-supplied                                                  | Filters applied before the RPC calls      |
| `selection.scan_ids_sha256`                   | exporter-supplied                                                  | sha256 of the sorted selected scan ids    |
| `recipe`                                      | object                                                             |                                           |
| `recipe.recipe_key`                           | `list_trait_recipes.recipe_key`                                    |                                           |
| `recipe.recipe_key_version`                   | `list_trait_recipes.recipe_key_version`                            |                                           |
| `recipe.recipe_kind`                          | `list_trait_recipes.recipe_kind`                                   |                                           |
| `recipe.chosen_by`                            | exporter-supplied                                                  | `default` when `is_default` was taken     |
| `recipe.definition`                           | `list_trait_recipes.definition`                                    |                                           |
| `recipe.observed`                             | object                                                             | Distinct values over the included sources |
| `recipe.observed.contract_versions`           | `cyl_trait_sources.metadata->'contract_version'`                   |                                           |
| `recipe.observed.traits_sleap_roots_versions` | `cyl_trait_sources.metadata->'traits_sleap_roots_version'`         |                                           |
| `recipe.observed.sleap_nn_versions`           | `cyl_trait_sources.metadata->'predict_models'->'sleap_nn_version'` | Per model entry                           |
| `recipe.observed.predict_container_digests`   | `cyl_trait_sources.metadata->'predict_container_digest'`           |                                           |
| `recipe.observed.traits_container_digests`    | `cyl_trait_sources.metadata->'traits_container_digest'`            |                                           |
| `recipe.observed.inference_configs`           | `cyl_trait_sources.metadata->'predict_inference_config'`           |                                           |
| `included`                                    | object                                                             |                                           |
| `included.n_scans`                            | `get_trait_recipe_coverage.status`                                 | Count of `included` rows                  |
| `included.source_ids`                         | `get_trait_recipe_coverage.source_id`                              | Of the `included` rows                    |
| `excluded`                                    | array                                                              |                                           |
| `excluded[].scan_id`                          | `get_trait_recipe_coverage.scan_id`                                |                                           |
| `excluded[].plant_qr_code`                    | `get_trait_recipe_coverage.plant_qr_code`                          |                                           |
| `excluded[].reason`                           | `get_trait_recipe_coverage.status`                                 |                                           |
| `excluded[].available_recipes`                | `get_trait_recipe_coverage.available_recipes`                      |                                           |
| `other_recipes_in_selection`                  | array                                                              |                                           |
| `other_recipes_in_selection[].recipe_key`     | `list_trait_recipes.recipe_key`                                    | Rows other than the chosen recipe         |
| `other_recipes_in_selection[].n_scans`        | `list_trait_recipes.n_scans`                                       |                                           |

## Changing the key

A change to a keyed field — `predict_models` triples, `predict_code_sha`, `traits_code_sha` or
`predict_output_params` — means a new `recipe_key_version` with new helpers. v1 values are never
rewritten, because exports cite them. The contracts re-pin procedure (`contracts/README.md`)
includes this check.
