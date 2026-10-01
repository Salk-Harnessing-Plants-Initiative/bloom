## ADDED Requirements

### Requirement: Trait export runs as an in-process job
The web app SHALL export traits through a job: `POST /api/cyl/trait-export/jobs` starts it, `GET /api/cyl/trait-export/jobs/{id}` reports its status, `GET /api/cyl/trait-export/jobs/{id}/download` returns its zip, and `DELETE /api/cyl/trait-export/jobs/{id}` cancels or drops it.

- **Request.** `POST` takes query-string parameters and an empty body: exactly one of `experiment` (optionally with `wave` and/or `age`) and `scan`, plus an explicit `recipe` and a `chosen` hint of `default` or `user`.
- **Job ids and ownership.** A job id is a random UUID, and a job is visible only to the verified user who started it. An id that is not a UUID, or is unknown, foreign, expired or lost to a restart, returns `404`.
- **Status.** One of `running`, `ready`, `failed` or `cancelled`, with the phase and the `done` and `total` batches. A `failed` job also carries a `detail`.
- **Download.**
  - A `ready` job returns the zip any number of times, until retention ends, its owner deletes it, or its owner's next job is accepted.
  - A `running`, `failed` or `cancelled` job returns `409` with a `detail`.
  - The zip response carries `Content-Type: application/zip`, `Content-Disposition: attachment; filename="<stem>.zip"` and `Cache-Control: no-store`.
- **DELETE** returns `204`. It cancels a running job, or drops a finished one.
- **Retention.** A finished job is kept for 600 seconds, measured exactly. A user holds at most one finished job: when their `POST` returns `202`, their previous finished job is dropped. A refused `POST` drops nothing.
- **Deadline.** A job still running 1,500 seconds after it started becomes `failed` with `detail` "the export took too long", and its slot is released.
- **`HEAD`** on every route returns `405`.

#### Scenario: Job lifecycle

- **WHEN** a signed-in user posts `experiment=E&recipe=K&chosen=default`
- **THEN** the response is `202` with a UUID job id
- **AND** polling reports `running` with increasing `done`, then `ready`
- **AND** the download returns a zip of exactly `<stem>.csv`, `<stem>.export.json` and `<stem>.excluded.csv`

#### Scenario: Download before the job is ready

- **WHEN** the download is requested while the job is `running`, or after it `failed` or was `cancelled`
- **THEN** the response is `409` with a `detail`

#### Scenario: Another user's job

- **WHEN** user B requests the status, download or `DELETE` of user A's job
- **THEN** the response is `404`, and A's job is unaffected

#### Scenario: Cancel

- **WHEN** the owner sends `DELETE` while the job is running
- **THEN** no further batch starts, in-flight calls are aborted, the status becomes `cancelled`, and the slot is released

#### Scenario: Retention

- **WHEN** a job finished 599 seconds ago
- **THEN** its status is returned
- **AND** at 601 seconds it returns `404`

#### Scenario: A refused start keeps the finished job

- **WHEN** a user with a ready job posts a request that is refused with `422`
- **THEN** the ready job can still be downloaded

#### Scenario: Deadline

- **WHEN** a job is still running 1,500 seconds after it started
- **THEN** its status becomes `failed` with `detail` "the export took too long", and its slot is released

### Requirement: Trait export reads one recipe in bounded batches
An export SHALL read trait data, recipes and coverage only through `list_trait_recipes`, `get_trait_recipe_coverage` and `get_experiment_traits`.

- **RPC arguments.** Each call carries `experiment_ids_ = [e]` (or `experiment_id_ = e`) and a non-empty `scan_ids_`: at most `BATCH_SCANS` ids for a coverage or trait call, and at most `LISTING_BATCH_SCANS` ids for a `list_trait_recipes` call. Every coverage and trait call carries the explicit `recipe_key_`.
- **Other reads.** Its other reads (`cyl_experiments`, `cyl_scans_extended`, `accessions`, `cyl_trait_sources`) use the verified user's token, and are keyset-paged or chunked.
- **Concurrency.** At most 3 PostgREST requests from this feature are in flight in the server process, across all jobs and listings.
  - Each job and each listing has at most 3 requests outstanding.
  - An aborted request keeps its place until 9 seconds after it was issued.
- **Merging listings across batches:**
  - `n_scans` is summed;
  - `newest_source_id` is the maximum;
  - `recipe_key_version`, `recipe_kind` and `definition` come from the batch holding that maximum (any batch for `unattributed`);
  - rows are ordered by `newest_source_id` descending, NULLs last, and the first row is the default.
- **Counts.** The selection is paged by ascending `scan_id` until an empty page, and must equal its exact count. Each trait call's rows must equal its exact count.
- **No retries.** No batch is retried.

#### Scenario: Batch size and completion order do not change the output

- **WHEN** the same selection, holding all four coverage statuses, is exported with `BATCH_SCANS` of 1, 2 and the selection size
- **AND** calls complete in issue order, in reverse and in random order, with rows shuffled within each response
- **AND** `generated_at` is fixed
- **THEN** every CSV, excluded CSV and sidecar is byte-identical

#### Scenario: Merged listing equals a single call

- **WHEN** a seeded selection's per-batch `list_trait_recipes` results are merged, for batch sizes 1, 2 and the selection size
- **THEN** the merged rows, their order and the default equal one call over the whole selection
- **AND** concatenated per-batch coverage equals one coverage call
- **AND** the union of per-batch trait rows equals one trait call

#### Scenario: A listing is one call for every current selection

- **WHEN** a selection of at most `LISTING_BATCH_SCANS` scans is listed, by the listing route or by a job, whatever `BATCH_SCANS` is
- **THEN** exactly one `list_trait_recipes` call is made, over the whole selection
- **AND** a selection of `LISTING_BATCH_SCANS + 1` scans is listed in two calls

#### Scenario: No call reads a whole experiment

- **WHEN** any export or listing runs, including one where a batch has no included scans
- **THEN** every RPC call carries a non-empty `scan_ids_` array
- **AND** a batch with no included scans makes no `get_experiment_traits` call

#### Scenario: Process-wide limit

- **WHEN** two jobs and one listing run at once
- **THEN** no more than 3 PostgREST requests from the feature are ever in flight

#### Scenario: Truncated trait response

- **WHEN** a trait call returns fewer rows than its exact count
- **THEN** the job fails with a `detail` naming the batch

### Requirement: Trait export fails rather than returning partial or mixed data
An export job SHALL fail, and SHALL serve no zip, when any PostgREST call errors or any integrity check fails. The checks are:

- **Coverage:** every selected scan has exactly one coverage row, and no row falls outside its batch. The `included` count equals the merged `n_scans` for the key.
- **Trait rows:**
  - every included scan returns trait rows;
  - every trait row's scan is one of the included scans requested in that call, and the row carries its scan's coverage `source_id` (NULL-safe);
  - no scan and trait pair repeats;
  - no trait name equals a fixed column name;
  - every value is a number, NULL, `"NaN"`, `"Infinity"` or `"-Infinity"`.
- **Metadata:** every non-NULL `accession_id` in the selection returns exactly one `accessions` row, and every included source returns exactly one `cyl_trait_sources` row.
- **Completeness:** the included and excluded scans together are exactly the selection.

Every `detail` is a fixed message, and never contains PostgREST's raw text:
- an RPC error names its code and "batch i of n", where i is the 1-based chunk index, except that `PGRST202`, `PGRST301` and `PGRST303` map to their own fixed messages;
- an integrity failure names the check and, where one applies, the scan;
- a changed selection says "the selection changed during the export; retry".

#### Scenario: A scan with no coverage row

- **WHEN** a selected scan returns no coverage row (for example, its plant has no accession)
- **THEN** the job fails with a `detail` naming the scan

#### Scenario: Statement timeout

- **WHEN** a trait call in batch 12 of 185 fails with `57014`
- **THEN** the job fails with a `detail` naming `57014` and "batch 12 of 185", and suggesting a wave or age filter
- **AND** the `detail` contains none of PostgREST's `message`, `details` or `hint`

#### Scenario: Data changed mid-export

- **WHEN** a new source of recipe `K` lands, between a batch's coverage and trait reads, for a scan that coverage marked `included`
- **THEN** the job fails with `detail` "the selection changed during the export; retry"

#### Scenario: Missing accession

- **WHEN** a selected scan's `accession_id` returns no `accessions` row
- **THEN** the job fails, rather than writing an empty `genotype`

### Requirement: Trait export records how its recipe was chosen
The job SHALL export exactly the requested recipe. It SHALL record `recipe.chosen_by` as `default` only when the hint is `default` and the key is still the merged default when the job runs, and as `user` otherwise.

A key absent from the selection's merged listing fails the job before any coverage call.

#### Scenario: Default kept

- **WHEN** the request says `chosen=default` for the selection's current default
- **THEN** `recipe.chosen_by` is `default`

#### Scenario: Default moved between preview and download

- **WHEN** the request says `chosen=default` for key `K`, but a newer recipe `K2` is the selection's default by the time the job runs
- **THEN** the file holds `K`
- **AND** `recipe.chosen_by` is `user`

#### Scenario: Key not in the selection

- **WHEN** `K` exists in experiment `E` but not among wave 2's scans, and `experiment=E&wave=2&recipe=K` is requested
- **THEN** the job fails with `detail` "this recipe is not in the selection", and makes no coverage call

### Requirement: Trait export file conventions
An export SHALL produce files that satisfy the following conventions. They are the web's part of the cross-exporter conventions that `_WIKI/SUPABASE/trait-recipes.md` §"Export sidecar v1" states for every exporter.

**The CSV:**
- one header row, then one row per included scan in ascending `scan_id`;
- columns, in order:
  1. the 22 metadata columns of bloomctl's `scans.csv`, in its order;
  2. `recipe_key` in full, then `source_id`;
  3. the traits present in the included rows, sorted by Unicode code point;
- a finite value is the shortest decimal that parses back to the stored float4;
- `NaN`, `Infinity` and `-Infinity` are written literally, and NULL or absent values are empty cells;
- RFC 4180 quoting, CRLF after every line, UTF-8 with no BOM, and no comment lines;
- a scan export has its own scan's trait columns.

**The excluded CSV** has the header `scan_id,plant_qr_code,reason,available_recipes`, with the recipes joined by `;`. It uses the same format as the CSV, and is header-only when nothing is excluded.

**The sidecar** validates against `trait-recipes.export.schema.json`, and:
- `selection.scan_ids` is null only for an unfiltered experiment, and otherwise the ascending selected ids;
- `selection.filters` holds integer `wave_number`, `plant_age_days` or `scan_id`;
- `scan_ids_sha256` is the sha256 of the ascending decimal ids joined by `,`;
- `included.source_ids` is the distinct non-NULL ids, ascending;
- `excluded` is exactly the non-`included` coverage rows in `scan_id` order, with `reason` equal to status;
- `other_recipes_in_selection` is `{recipe_key, n_scans}` in listing order;
- `observed` is present only for pipeline recipes, with sorted distinct values;
- free-form objects have their keys sorted recursively.

**The stem** is `<slug>[_wave<W>][_day<A>][_scan<id>]_<keyseg>_<yyyymmdd>`, where `<keyseg>` is the first 8 characters of a 64-hex key, `legacy-<N>` for `legacy:N`, or `unattributed`.

#### Scenario: Trait columns follow the data

- **WHEN** recipe `K`'s included rows carry lateral-root traits, and a `legacy_only` scan carries crown-root traits
- **THEN** `K`'s header holds the lateral trait names and none of the crown trait names

#### Scenario: Values are the stored float4

- **WHEN** a stored value is the float4 nearest 0.1, and PostgREST renders it as `0.100000001490116`
- **THEN** the cell is `0.1`

#### Scenario: NULL, NaN and absent values

- **WHEN** one included scan has a NULL value and a `"NaN"` value, and another lacks a trait entirely
- **THEN** the NULL and the absent cells are empty, and the NaN cell is `NaN`

#### Scenario: Excluded scans

- **WHEN** a selected scan has only pipeline recipe `K2`, one has only `legacy:9`, one has only `unattributed` rows, and one has no traits, and `K` is exported
- **THEN** none of them is in the CSV
- **AND** `excluded` lists them in `scan_id` order with reasons `other_recipe`, `legacy_only`, `legacy_only` and `no_traits`
- **AND** `included.n_scans` plus the number of excluded scans equals the number of selected scans

#### Scenario: A superseded source is not exported

- **WHEN** an included scan also has an older source of `K`
- **THEN** neither that source's id nor its values nor its `observed` values appear in the export

#### Scenario: Unattributed recipe

- **WHEN** recipe `unattributed` is exported
- **THEN** the sidecar validates, with `included.source_ids` equal to `[]`, a null `definition` and no `observed`
- **AND** every CSV `source_id` cell is empty

#### Scenario: Filtered selection

- **WHEN** wave 0 and age 0 are exported
- **THEN** `selection.filters` is `{"wave_number": 0, "plant_age_days": 0}`, and `selection.scan_ids` is the full ascending selected ids with the matching `scan_ids_sha256`

#### Scenario: Legacy stem and source ids

- **WHEN** experiment "Diversity Screen" is exported for wave 3, age 0 and recipe `legacy:12345` on 2026-10-02 UTC
- **THEN** the stem is `diversity-screen_wave3_day0_legacy-12345_20261002`
- **AND** `included.source_ids` is `[12345]`

#### Scenario: Scan id hash

- **WHEN** the selected scans are 12, 3 and 7
- **THEN** `scan_ids_sha256` is the sha256 of the bytes `3,7,12`

#### Scenario: A scan export agrees with the experiment export

- **WHEN** scan `s` is `included` for `K`, and is exported alone with `scan=s&recipe=K`
- **THEN** for every column in the scan export's header, its cell equals the experiment export's cell for `s` in that column
- **AND** every experiment-export trait column absent from the scan export's header is empty for `s`

### Requirement: Trait export metadata matches bloomctl
The export's 22 metadata cells for a scan SHALL equal the string cells bloomctl's `scans.csv` writer produces for the same `cyl_scans_extended` row and genotype.

#### Scenario: Metadata edge cases

- **WHEN** the web renders rows with a NULL and a zero plant age, a NULL wave number, a NULL `date_scanned`, and QR codes with `/`, `:`, `\`, a trailing space and only dots
- **THEN** every cell equals the cell bloomctl writes for the same row

### Requirement: Trait export routes guard access and limit jobs
The trait export routes SHALL verify the signed-in user's token against GoTrue, and SHALL make every read with that token (never a service key). Because `cyl_scans_extended` does not apply RLS, the RLS-filtered `cyl_experiments` visibility check SHALL decide access. A request refused before that check SHALL make no PostgREST call.

The job route checks, in order:

| Check | Response |
|---|---|
| `Sec-Fetch-Site` is present and not `same-origin` | `403` |
| No session, or GoTrue rejects the token | `401` |
| GoTrue is unreachable or errors | `503` |
| Fewer than 1,800 seconds left on the verified token | `401` with `detail` "session expires too soon" |
| Malformed parameters | `422` |
| The user has a running job (the body carries its `job_id`), 2 jobs are running, or the memory budget is full | `429` |
| The experiment is not visible through RLS (for a scan, the scan's experiment), or the selection is empty | `404` |
| The paged selection differs from its exact count, or repeats an id | `409` with `detail` "the selection changed during the export; retry" |
| A PostgREST error during the selection | `502` with a fixed `detail` |

Malformed parameters are any of:
- an integer not written `0` or `[1-9][0-9]*` (at most 15 digits);
- a non-positive `experiment` or `scan`;
- a repeated parameter;
- `wave` or `age` given with `scan`;
- not exactly one of `experiment` and `scan`;
- a bad `recipe` pattern;
- a `chosen` other than `default` or `user`.

The slot is taken before any PostgREST call, and is released however the job ends, including a `404`, `409` or `502`.

The other routes:
- **Listing** applies the same checks without the session floor, the `recipe` and `chosen` parameters, and the job limits. It returns `200` with `n_selected` 0 for an empty selection of a visible experiment.
- **Status, download and delete** apply the first three checks and job ownership.

#### Scenario: Unverified identity

- **WHEN** a request carries a session cookie whose token GoTrue rejects
- **THEN** every trait export route returns `401`, no slot is taken, and no PostgREST call is made

#### Scenario: Deleted experiment

- **WHEN** a `bloom_user` starts an export, or lists recipes, for an experiment whose `deleted_at` is set, or for a scan in one
- **THEN** the response is `404`, even though `cyl_scans_extended` would return its scans
- **AND** any slot taken is released

#### Scenario: Selection changed before the job starts

- **WHEN** the paged selection differs from its exact count
- **THEN** the job route returns `409` with `detail` "the selection changed during the export; retry", and the slot is released

#### Scenario: Age zero is valid

- **WHEN** a request has `age=0`
- **THEN** the selection is the scans with `plant_age_days = 0`

#### Scenario: Malformed parameters

- **WHEN** a request has `age=-1`, `age=07`, `wave=3&scan=5`, `recipe=abc`, or both `experiment` and `scan`
- **THEN** the response is `422` and no PostgREST call is made

#### Scenario: Cross-site request

- **WHEN** any trait export route receives `Sec-Fetch-Site: cross-site`
- **THEN** the response is `403` and no PostgREST call is made

#### Scenario: One job per user

- **WHEN** a user with a running job starts another
- **THEN** the response is `429` carrying the running job's `job_id`, and no PostgREST call is made
- **AND** after the first job ends, a new request is accepted

#### Scenario: Session near expiry

- **WHEN** the verified token has 1,000 seconds left
- **THEN** the job route returns `401` with `detail` "session expires too soon"

#### Scenario: Listing error

- **WHEN** a `list_trait_recipes` batch fails
- **THEN** the listing route returns `502` with a fixed `detail` naming the code and batch

### Requirement: Trait export recipe listing
`GET /api/cyl/trait-export/recipes` SHALL return `n_selected` and the merged recipe rows (`recipe_key`, `recipe_kind`, `recipe_key_version`, `definition`, `n_scans`, `newest_source_id`, `is_default`) for the same selection parameters as a job. A user's newer listing SHALL abort their older one.

#### Scenario: Each row says what its recipe is

- **WHEN** a selection holds a pipeline, a legacy and an unattributed recipe
- **THEN** the pipeline row's `definition` carries its `models` and code SHAs, the legacy row's its `source_id` and `source_name`, and the unattributed row's is `null`, each as `list_trait_recipes` returned it

#### Scenario: Filtered listing

- **WHEN** the dialog requests `experiment=E&wave=2`
- **THEN** `n_selected` equals wave 2's scan count
- **AND** exactly one row has `is_default` when any recipe exists

#### Scenario: No recipes

- **WHEN** every selected scan has no traits
- **THEN** the response has `n_selected` equal to the selection size and no rows

#### Scenario: Empty selection

- **WHEN** no scan of a visible experiment matches the wave and age
- **THEN** the response is `200` with `n_selected` 0 and no rows

### Requirement: Trait download dialog on the traits and scan pages
The traits page SHALL offer an experiment-grain "Download traits" dialog, enabled once the page's waves and ages have loaded. The scan page SHALL offer a scan-grain one.

The dialog:
- lists the selection's recipes, with the default preselected and labelled, each recipe's included count out of `n_selected`, and what the recipe is, from its `definition`: a pipeline recipe's models (name and version) and code SHAs, a legacy recipe's source name, or "no source recorded" for unattributed;
- on the traits page, offers wave and age filters, prefilled from the page's current wave and age when the loaded lists contain them, each allowing "All". It re-lists on change, ignores stale responses, and says so when no scans match;
- disables Download when there are no scans or no recipes;
- refreshes the browser session before listing and before starting a job, and retries once after a "session expires too soon" `401`;
- offers to resume or cancel the running job returned by a `429`;
- shows progress while polling, and saves the zip when it is ready;
- shows a failure's `detail` with Retry, and shows "the export was interrupted" when polling returns `404`;
- cancels the job when closed;
- links to the "Using a trait export" section of `trait-recipes.md`.

#### Scenario: Default preselected

- **WHEN** the dialog opens on an experiment with two recipes
- **THEN** the default recipe is selected and labelled as the default

#### Scenario: Failure shown

- **WHEN** the job fails with a `detail` naming a timeout in batch 12
- **THEN** the dialog shows that `detail` and a Retry button, and saves nothing

#### Scenario: Interrupted export

- **WHEN** polling returns `404` for a running job
- **THEN** the dialog says the export was interrupted and offers Retry

#### Scenario: Close cancels

- **WHEN** the dialog is closed while the job is running
- **THEN** it sends `DELETE` for the job and stops polling
