## Why

Implements #481. User feedback (Ryan) on the cylinder data surfaces: bulk work is not possible.
Selecting and downloading images is effectively one-at-a-time, image data and trait data cannot be
pulled together, and subsetting by trait is limited to eyeballing a single trait's distribution.

The CLI is where bulk belongs — `0.1.0a3` already shipped `batch-download-for-predict` and
`batch-ingest-result` for the pipeline — but the human-facing read/download path never got the same
treatment:

- `bloomctl cyl download` resolves **exactly one** experiment or **one** scan. Its selectors
  (`--experiment-id` / `--scan-id` / `--experiment-name`) are mutually exclusive, and
  `fetch_scans()` takes a scalar `experiment_id` with a scalar `--plant-qr-code` applied as
  `.eq("qr_code", ...)`. There is no way to pass a list of barcodes, several experiments, or an
  accession.
- `cyl datasets` (trait datasets) and `cyl download` (images) are disjoint. No command joins a scan
  to its traits, so "give me the images *and* the traits for this subset" requires two exports and a
  manual join on `scan_id`.
- Nothing selects scans *by trait value*. With hundreds of SLEAP traits available, the only way to
  subset is to already know which scans you want.

The selection machinery to fix this was already built — for the web search bar, in #516. That change
added the `cyl_plant_search` view and the `cyl_plant_search_query` function, which accepts a
**barcode array** plus accession / species / experiment id filters (capped at 5000 entries per
filter, 500 rows per page), and the web search page already batches a pasted barcode list through
it. It has been live since the 2026-07-29 promotion.

This proposal reuses that layer to enable bulk downloads. The intent is explicitly *not* to build a
second selection path: the querying, the caps, and the permission model are done and running in
production — they are simply unreachable from the command line.

## What Changes

### Accession counts say what they count

- **`count(*)` stays.** An earlier draft proposed `count(DISTINCT qr_code)` on
  `cyl_accession_sample_counts`, on the premise that one barcode across several waves is one sample
  counted repeatedly. That premise is wrong.
  `20230724171639_add_uniqueness_contraints.sql:5` makes `cyl_plants` unique on
  `(wave_id, qr_code)`, so a barcode reappearing in a later wave is **a different plant that reuses
  the tag**, not the same one rescanned — `refactor-supabase-reader-db-tier2/design.md` (D5) already
  settled this, calling tag reuse across waves "an ordinary operational occurrence". Collapsing on
  `qr_code` alone would silently merge distinct plants into one number, which is a worse error than
  the one it set out to fix. `count(*)` is already the correct distinct-sample count under that
  constraint.
- **The narrow question that remains is null barcodes.** `qr_code` is nullable, so barcode-less
  plant rows are included in the total, and the output gives no way to see it. That is a real but
  much smaller issue than the draft claimed, it is independent of this change, and it should be
  measured before anything is altered — the fix may be nothing more than reporting the null count
  alongside the total. **Split out and settled separately; no view change ships in this proposal.**
- **The column keeps the name `plant_count`.** Renaming it to `barcode_count` only made sense under
  the abandoned semantics; a plant count is what it is and what it should say.
- **Visible change: none.** `cyl accessions sample-counts` returns exactly what it returns today.
  The draft would have dropped counts for every accession whose barcodes span multiple waves —
  a silent, unannounced change to a number scientists already cite.

### Bulk and subset selection for `cyl download`

- Accept **multi-valued** selectors, replacing the current one-experiment-or-one-scan rule:
  `--experiment-id` and `--scan-id` become repeatable; add `--barcodes-file <path|->` (a newline or
  JSON list, `-` for stdin), `--barcodes a,b,c`, `--accession NAME`, and `--species NAME`.
- Selectors **compose as an intersection** (`--species soybean --accession Col-0` = plants that are
  both), replacing today's mutual exclusion. `--experiment-name` keeps its existing
  resolve-then-download behaviour and its ambiguity guard.
- Add `--dry-run` to print the resolved selection (scan count, experiment count, estimated image
  count) and exit without downloading, so an accidental multi-thousand-image pull is visible first.
- Route resolution through the existing `cyl_plant_search_query` RPC rather than new SQL, inheriting
  its filter-size cap and `SECURITY INVOKER` semantics.

### Trait discovery

- Add `cyl traits list` — for each trait, its name, source, scan count, and the observed **minimum
  and maximum** value. It accepts the same selectors as `cyl search`, so the range describes the set
  being looked at rather than the whole database.
- Without this, `--trait NAME:MIN:MAX` cannot be used: trait scales differ, values may be fractional,
  and nothing in the CLI reports what a trait spans. `cyl datasets get` lists trait *names* for one
  dataset and no values. A range has to be seen before it can be chosen.
- Trait names come from the existing `cyl_scan_trait_names` view; the aggregate and the
  experiment-scoping are new. A name under several sources is reported per source, never merged into
  one range.
- **Trait reads take the latest source, and say which one they took.** No `--source` flag and no
  `list-sources` command in this change: only 2 of 224 experiments have more than one source (#626,
  2026-08-07), and where choosing does matter the shape is already settled — #644 (merged) gave
  bloommcp `list_sources()` / `resolve_source()` over the `list_experiment_trait_sources` RPC, and
  `get_experiment_traits` already takes `source_id_` / `run_id_`. The CLI wires to that when it needs
  it rather than growing a second convention. Tracked against #626.
- **`get_experiment_traits` is left untouched.** It already exists, is `SECURITY INVOKER`, is granted
  to the read roles, and returns per-scan trait rows for one experiment (`scan_id`, `plant_qr_code`,
  `accession_name`, `trait_name`, `source_id`, `trait_value`). The CLI does not call it. It is not
  widened, for the same reason as above — a new parameter overloads rather than replaces — and it
  carries integration tests asserting row-for-row parity with `get_scan_traits` plus its EXECUTE
  grants. The new trait queries **return the same column shape** so the two agree, and
  `get_experiment_traits` keeps serving its existing per-experiment contract unchanged.

### Trait-linked download

- Add `--trait NAME[:MIN][:MAX]` as a **selector** on `cyl download`, the same predicate
  `cyl traits select` uses and routed through the same query, so the two cannot drift. Selecting is
  independent of output: `--trait` decides which scans are downloaded, `--with-traits` /
  `--traits-only` decide which files are written.
- Add `--with-traits` to `cyl download`: alongside `scans.csv`, write `traits.csv` for the same
  resolved scan set, read through the existing source-aware trait views (`cyl-trait-read`), joined
  on `scan_id`.
- Add `--traits-only` to pull the trait table for a selection without fetching images.
- Add `cyl traits select` — resolve a set by **trait predicate**, written as a self-contained token
  so bounds can't detach from their trait: `--trait primary_root_length:100:400`, repeatable and
  intersecting, either bound omittable.
- `cyl traits select` accepts **the same selectors as `search` and `download`**
  (`--barcodes-file`, `--barcodes`, `--accession`, `--species`, `--experiment-id`) as a pre-filter,
  so you can shortlist within a barcode list you already hold: "of these 200 barcodes, which have
  primary root length between 100 and 400". Selectors and predicates resolve in **one server-side
  query**, not by intersecting two result sets in the client.
- `--grain scan|barcode` (default `scan`) selects the answer's granularity, because a trait value
  belongs to a scan while a plant is scanned repeatedly. `scan` emits one row per matching scan and
  feeds `cyl download --scan-ids-file`; `barcode` emits one row per barcode with `scans_matched` and
  `scans_total`, and `--match any|all` decides whether one qualifying scan suffices. Both grains emit
  `qr_code` and `scan_id`, so a barcode list goes in and a barcode list comes back out.

### Multi-field and batch search in the CLI

- Add `cyl search` — the CLI counterpart of #516's web search page, over the same
  `cyl_plant_search` view. Accepts a batch barcode list (file or stdin), `--accession`, `--species`,
  `--experiment`, with `--output csv|json`. **"PI" is the accession name**, so PI search is
  accession search; no new schema field is introduced.
- The feedback's "search by species" and "batch barcode list" asks are already satisfied on the web
  by #516. Bringing the same capability to the CLI is what is new here; making the existing web
  capability discoverable is a documentation task, tracked separately.
- Emits `scan_id` / `qr_code` columns that feed directly into the download and batch commands, so
  search → select → download is one pipeline.
- Add `--show plants|experiments|accessions|species` (default `plants`) so any filter can be answered
  with any other field — give a barcode, get its accession/species/experiment; give a species, get its
  experiments and accessions. `cyl_plant_search` already carries all of them on every row, so no new
  view is needed. Rollups are computed server-side across the whole match, because de-duplicating a
  500-row page in the client would return only the values present in that page.

### Explicitly not in this change

- **The Bloom Assistant erroring on every prompt** is a defect, not a capability gap. It restores
  intended behaviour and is out of scope here.
- **The web bulk-download UI**, tracked in #482. Bulk lands in the CLI first and the web surfaces are
  unchanged *in this change* — but every server-side piece added here MUST be reachable from the web
  with no further backend work, so #482 becomes a front-end task only. Selection logic written as
  CLI-only Python is explicitly out of bounds.
- **A "rep ID" field.** Tracked separately in #501, which already establishes that `cyl_plants`
  carries only `qr_code`, `wave_id` and `accession_id` — there is no replicate-ID column to match on.
  Nothing here changes that.

## Related issues

### Implements

- **#481** — feat(bloomctl): bulk image + trait download and flexible selection. This proposal is the
  design for that issue; its checklist (traits in `download`, selection filters, barcode lists,
  trait-threshold selection) maps onto the work below.

### Builds on — already shipped

- **#516 / #517 / #518** — CYL plant search: the `cyl_plant_search` view and
  `cyl_plant_search_query`. The selection layer this reuses rather than rebuilds.
- **#449** `cyl experiments list`, **#451** `cyl accessions` (list + sample-counts), **#452**
  `cyl qc list-sets`, **#441** `cyl datasets list + create`, **#450** read-model views — the Find
  commands this plugs into.
- **#347** — the original `bloomctl` CLI this all sits inside.

### Resolved as a side effect

- **#384** — `cyl download` filter follow-ups. Three of its five items disappear with this rewrite:
  filters silently ignored in `--scan-id` mode and `--plant-qr-code` overriding the age window both
  go away once selectors intersect instead of branching, and `--limit` truncating silently is fixed
  by paging. Its empty-result and filter-unit-test items are covered by the specs here.

### Needs coordinating — open and overlapping

- **#496** — per-experiment `sample-counts`. **No longer a conflict.** An earlier draft of this
  proposal redefined `cyl_accession_sample_counts` (`count(*)` → `count(DISTINCT qr_code)`, column
  renamed `plant_count` → `barcode_count`), which collided head-on with #496's *"Do not modify the
  existing `cyl_accession_sample_counts` view"*. That redefinition is gone, so #496 can add its
  `cyl_experiment_accession_sample_counts` sibling view as designed, and neither blocks the other.
  The one thing to keep aligned: the sibling view should count the same way the global one does —
  `count(*)` over `cyl_plants`, which is already the correct distinct-sample count under
  `cyl_plants_uniqueness UNIQUE (wave_id, qr_code)` — so the two views cannot come to disagree about
  what "count" means. Nothing to decide before either is implemented.
- **#482** — web bulk selection/download. Unblocked by this work: every query added here is a shared
  read model, so #482 becomes a front-end task with no new backend.
- **#501** — rep-ID search. Out of scope here; #501 already establishes there is no replicate-ID
  column on `cyl_plants` to match on.
- **#528** — RPC-based navigation on search, and **#527** — refactoring plant search into a shared
  component. Both touch the same search layer; neither is changed here, but `--show` adds a rollup
  query they may want to use.

### Adjacent, deliberately untouched

- **#534 / #536** — concurrent image downloads. Orthogonal: those change how fast frames download,
  this changes which scans are selected. They compose.
- **#525** — long downloads failing and unable to resume. Bulk selection makes larger downloads
  easier to start, so this becomes more visible, not less.
- **#533** — `batch-download-for-predict` racing on a shared `out_dir`.
- **#499** — `cyl qc list-sets` counting QC codes in the database.

## Impact

- **Affected specs:** `cyl-accession-counts` (new), `cyl-bulk-selection` (new),
  `cyl-trait-linked-download` (new), `cyl-plant-search-cli` (new). Reads from the existing
  `cyl-trait-read` capability; does not modify it.
- **Affected code:** `bloomcli/src/bloomctl/cyl/download.py` (selector resolution, `fetch_scans`
  scalar→list), `bloomcli/src/bloomctl/cyl/_select.py`, new `search.py` and `traits.py` command
  modules, `bloomcli/src/bloomctl/cyl/datasets.py` (trait join).
- **Migrations (three):**
  1. A **separate** rollup query returning DISTINCT experiments, accessions or species across the
     whole match — what `--show` reads. `cyl_plant_search_query` is **not** modified: adding a
     defaulted parameter creates a function overload rather than replacing it, and PostgREST resolves
     RPCs by the named arguments supplied, so an overload risks ambiguous resolution for the live web
     search box. A separate function carries no risk to #516 at all.
  2. A trait aggregate query returning each trait's name, source, scan count, minimum and maximum for
     the supplied filters — what `cyl traits list` reads.
  3. A trait-predicate query ("scans whose trait X falls in a range") — what `cyl traits select` and
     `cyl download --trait` both read.

  All three use the same `SECURITY INVOKER` model and role grants as `cyl_plant_search_query`, so the
  web can call them too.
  Selection and search themselves need no migration — `cyl_plant_search`, `cyl_plant_search_query`
  (#516), and the source-aware trait views already exist on `main`.
- **No reported number changes.** `cyl accessions sample-counts` is untouched, so nothing that is
  already published moves. The one breaking change in this proposal is `cyl download`'s selectors
  intersecting instead of excluding each other (PR 3).
- **Reusability constraint:** any database view or function added here is a shared read model, not a
  CLI helper. Acceptance test for each one — *could the web app do a bulk download using this
  without new backend work?* If not, it is in the wrong layer.
- **BREAKING (CLI surface):** `--experiment-id` and `--scan-id` stop being mutually exclusive. A
  script relying on the current "specify exactly one" error is unaffected in the success path;
  only the error case changes.
- **Resolves three of #384's filter follow-ups** as a side effect of the rewrite: filters silently
  ignored in `--scan-id` mode and `--plant-qr-code` silently overriding the age window both disappear
  once selectors intersect instead of branching, and `--limit` silently truncating is fixed by paging.
  #384's remaining items (empty-result exit code, filter unit tests) are covered by the specs here.
- **Adjacent known defect, not fixed here:** `fetch_experiments_with_accessions()` and
  `fetch_species_with_accessions()` fetch unbounded and can silently truncate their menus — unlike
  `fetch_experiments`, which bounds and warns. Worth its own fix.
