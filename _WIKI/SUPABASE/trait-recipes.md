# Trait recipes and exports

A **recipe** is how a set of cyl trait values was computed: which models, which predict and
traits code, and which output-defining predict parameters. Pipeline write-back stores one
`cyl_trait_sources` row per scan, so an experiment's latest values can mix recipes. The recipe
key lets a reader take one recipe across a whole selection, and say which scans that leaves out.

The recipe rules live in the specs; this page explains them. It also defines the export file
conventions that every exporter (the web download, bloomctl and bloommcp) follows.

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
- **Selections** are experiments, scans, or both (intersected). A NULL `scan_ids_` means the whole
  experiment. An exporter reads in scan batches instead (see "Batched exporters" below).
- **Performance boundary:** a single recipe read of a large experiment exceeds the 8 s limit
  (experiment 1's `legacy:5` alone is about 13.9M rows; bloom#936). Batched reads keep each call
  small.

**Datasets.** `create_cyl_dataset` takes exactly one of `trait_source_id` and `recipe_key`.
`cyl_datasets.recipe_key` records the recipe the function froze. A source-mode dataset stores its
source's recipe too, so `trait_source_id IS NULL` marks recipe mode. The function is the only check:
a direct write to `cyl_datasets` (which some roles' policies allow) can set a `recipe_key` that
does not match the frozen rows, just as it can for `trait_source_id`. A PostgREST recipe-mode call
still sends every argument:

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
| `<stem>.excluded.csv` | Flat copy of the excluded list. Optional; the web download always writes it                                          |

- **The format.** The schema is [`trait-recipes.export.schema.json`](trait-recipes.export.schema.json),
  and there is an illustrative example in [`trait-recipes.export.example.json`](trait-recipes.export.example.json).
  Its keys and ids are made up.
- **The stem** is `<slug>[_wave<W>][_day<A>][_scan<id>]_<keyseg>_<yyyymmdd>` (see "Stem" below).
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
| `selection.scan_ids`                          | exporter-supplied                                                  | The selected ids, or null (see below)     |
| `selection.filters`                           | exporter-supplied                                                  | Integer selectors applied (see below)     |
| `selection.scan_ids_sha256`                   | exporter-supplied                                                  | See "Selection" below                     |
| `recipe`                                      | object                                                             |                                           |
| `recipe.recipe_key`                           | `list_trait_recipes.recipe_key`                                    |                                           |
| `recipe.recipe_key_version`                   | `list_trait_recipes.recipe_key_version`                            |                                           |
| `recipe.recipe_kind`                          | `list_trait_recipes.recipe_kind`                                   |                                           |
| `recipe.chosen_by`                            | exporter-supplied                                                  | `default` only if still the default       |
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

### Export file conventions

These are the rules an exporter follows so its files are **interchangeable** with any other
exporter's: the same columns and order, the same cell values, the same row and list orders, and
the same `scan_ids_sha256`. Exporters may differ only in the sidecar's JSON formatting (whitespace
and key order). The web download's own guarantees are in `openspec/specs/cyl-trait-export/spec.md`.

**CSV layout.**

1. The 22 metadata columns of bloomctl `cyl download`'s `scans.csv`, in its order (`CSV_COLUMNS` in
   `bloomcli/src/bloomctl/cyl/download.py`), with the same values. `genotype` is `accessions.name`.
   `scan_path` names bloomctl's image directory for the scan, so the file joins a `cyl download`
   folder; it is not a path inside an export.
2. `recipe_key` in full (never shortened: a short hex prefix can parse as a number), then
   `source_id`, which is empty for `unattributed`.
3. The traits present in the included rows, sorted by Unicode code point.

- One header row, then one row per included scan, in ascending numeric `scan_id`.
- A per-scan export has the trait columns of its own scan. It agrees with an experiment export by
  column name.
- RFC 4180 quoting (only cells with `,`, `"`, CR or LF), CRLF after every line including the last,
  UTF-8 without a BOM, and no comment lines.

**Values.** `cyl_scan_traits.value` is `real`, so every value is a float4. A finite value is written
as the shortest decimal that parses back to the same float4: `0.1`, not `0.10000000149011612`
(Python: `str(numpy.float32(v))`, which spells some values differently, such as `90.0` or `1e-07`, with the same value). Non-finite values are written `NaN`, `Infinity` and `-Infinity`.
A NULL value, or a trait the scan lacks, is an empty cell. Cells compare as float4 values, not
byte for byte.

**Selection.**

- `scan_ids` is null only for a whole, unfiltered experiment; otherwise it is every selected scan
  id, ascending (not the per-call `scan_ids_` of a batched read).
- `filters` records the selectors applied, with integer values: the web writes `wave_number`,
  `plant_age_days` or `scan_id`. Other exporters may add keys named for their own selectors, with
  integer or integer-array values.
- `scan_ids_sha256` is the lowercase hex sha256 of the UTF-8 bytes of the ascending decimal ids
  joined by `,`. The scans 12, 3 and 7 hash the bytes `3,7,12`.
- `chosen_by` is `default` only when the caller took the default and it is still the selection's
  default when the export runs; otherwise `user`.

**Orderings.** `included.source_ids` is the distinct non-NULL source ids, ascending (`[]` for
`unattributed`, `[5]` for `legacy:5` however many scans it covers). `excluded` is in `scan_id`
order. `other_recipes_in_selection` is in `list_trait_recipes` order. Each `observed` array holds
distinct values sorted by code point; `inference_configs` are deduplicated and sorted by their
key-sorted JSON; `observed` is present only for pipeline recipes.

**Excluded CSV.** The header is `scan_id,plant_qr_code,reason,available_recipes`, with the recipes
joined by `;`, in the same format as the traits CSV. It is header-only when nothing is excluded.

**Stem.** `<slug>[_wave<W>][_day<A>][_scan<id>]_<keyseg>_<yyyymmdd>`:

- `<slug>` is the experiment name with each run of characters outside `[A-Za-z0-9-]` replaced by
  `-`, lowercased, cut to 60 characters and trimmed of `-`; `experiment-<id>` if nothing remains.
- `<keyseg>` is the first 8 characters of a 64-hex key, `legacy-<N>` in full for `legacy:N`, or
  `unattributed`.
- `<yyyymmdd>` is the UTC date of `generated_at`.
- An exporter whose filters have no segment here may leave them out; the sidecar records them.

**Batched exporters.** A large selection is read in scan batches, each call under PostgREST's 8 s
limit:

- pass an explicit `recipe_key_` to every coverage and trait call (NULL means "latest", which mixes
  recipes) and never a NULL `scan_ids_` (which means the whole experiment);
- list recipes in as few `list_trait_recipes` calls as possible: one call over all of experiment 1's
  18,471 scans took 1.26 s on staging (2026-10-01), and the web exporter lists up to 20,000 scans
  per call. If a selection is split, merge the per-batch rows: sum `n_scans`, take the max
  `newest_source_id` and the other fields from the batch holding it, order by `newest_source_id`
  descending with NULLs last, and take the first as the default;
- check each trait read's row count against PostgREST's exact count, and page scan selections by
  keyset until an empty page;
- fail rather than write a partial or mixed file if any call errors, any selected scan lacks a
  coverage row, or the included count disagrees with the merged listing.

## Using a trait export

**Getting one.** Click "Download traits" on an experiment's traits page or on a scan's page. On
the traits page the dialog starts on the page's wave and plant age; set both to All to export the
whole experiment. The dialog lists the recipes in your selection, each with how many of the
selected scans it covers and what produced it (models with their versions and weights checksums,
code versions and any output params, or a legacy source). **The preselected default is the newest
recipe, not the one covering the most scans.** After a new pipeline image it may cover only a few
scans; the dialog then names the recipe that covers the most, and you can pick that one instead.
Recipes differ in models and trait columns, so use one recipe per analysis rather than combining
files from different recipes. Scans the chosen recipe doesn't cover are listed in
`<stem>.excluded.csv`. The dialog says "Download started" once the browser has the file; "Save
again" saves it again while the dialog is open.

An export is one recipe's traits for a selection of scans, as a zip of three files:

- `<stem>.csv`: one row per scan that has the recipe;
- `<stem>.export.json`: which recipe it is (models and code versions), what was selected, and every
  selected scan left out, with the reason;
- `<stem>.excluded.csv`: the left-out scans as a table.

**How it differs from the CSV passed around through Box.**

- `genotype` (the accession name) is added after `accession_id`.
- `recipe_key` and `source_id` are added; every row has the same `recipe_key`.
- There are no `primary`, `crown`, `lateral` (`.slp` file names) or `plant_name` columns.
- `scan_path` is where `bloomctl cyl download` puts the scan's images, not a path in the zip.
- The trait columns depend on the recipe's models: a crown-root recipe has `crown_*` traits, a
  lateral-root recipe `lateral_*`.
- Values are the stored single-precision values in shortest form. To compare with a file written
  from double-precision numbers, round both to float32 first.

**Loading it.** In sleap-roots-analyze:

```python
from sleap_roots_analyze.data_cleanup import load_trait_data

df = load_trait_data(
    "export.csv",
    barcode_col="plant_qr_code",
    genotype_col="genotype",
    replicate_col="scan_id",
)
```

`get_trait_columns` drops any column whose name contains `index`, `date`, `time`, `day_`, `scan_`
and similar, which includes real traits such as `curve_index_median`. Pass your trait columns
explicitly (every column after `source_id`) instead of relying on it. bloommcp's `qc_clean` finds
`genotype` and `plant_qr_code` by name, but takes inline CSV text of at most 5 MiB: a whole large
experiment is far over that (experiment 1's `legacy:5` CSV is about 110 MB), so export one wave or
age for it.

`load_trait_data` reads with pandas' defaults, which turn all-digit QR codes into numbers (dropping
leading zeros) and a genotype named `NA` or `None` into missing. If your data has either, read the
file with pandas yourself:

```python
import pandas as pd

df = pd.read_csv(
    "export.csv",
    dtype={"plant_qr_code": str, "genotype": str},
    keep_default_na=False,
    na_values=["", "NaN"],
)
```

That keeps identifiers as text, and an empty cell (no value, or no row for that trait) and `NaN`
both read as missing, as they do by default.

**Things to know.**

- A cell starting with `=`, `+`, `-` or `@` is written as-is, so a spreadsheet may treat it as a
  formula.
- There is no byte-order mark, so a spreadsheet may mis-read accession names with non-ASCII
  characters; import it as UTF-8.
- A whole-experiment export mixes plant ages. The sleap-roots Pipeline class is chosen by age
  window and is not part of the recipe key (see "Known blind spot" above), so filter by
  `plant_age_days`, or check it, before pooling ages.
- `unattributed` (trait rows with no source recorded) and a `pipeline` recipe whose payload is
  empty both mean the provenance is unknown: the rows may not all come from the same computation.

## Changing the key

A change to a keyed field — `predict_models` triples, `predict_code_sha`, `traits_code_sha` or
`predict_output_params` — means a new `recipe_key_version` with new helpers. v1 values are never
rewritten, because exports cite them. The contracts re-pin procedure (`contracts/README.md`)
includes this check.
