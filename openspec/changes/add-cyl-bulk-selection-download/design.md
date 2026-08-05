## Context

### The problem

Three things are missing from the cylinder data surfaces:

1. You can't download a batch of images. `cyl download` takes one experiment, or one scan, or one
   barcode — never a list.
2. You can't get images and trait numbers together. Images come from one command, traits from
   another, and you join them by hand.
3. You can't pick scans by a trait value. With hundreds of traits available, the only way to narrow
   down is to already know which scans you want.

Finding data and fetching data are separate worlds. You can work out exactly what you want, then have
no way to go and get it.

### What #516 already gives us

The hard part was built for the website last month. PR #516 added:

- **`cyl_plant_search`** — one row per plant, carrying barcode, accession, species, experiment and
  wave. Soft-deleted experiments are already excluded.
- **`cyl_plant_search_query`** — a saved query taking **lists**: barcodes, accessions, species,
  experiments. Returns every plant matching all of them. Runs with the calling user's own
  permissions, so people only see what they're allowed to see.

It is live in production and is what the website's search box uses when you paste in a column of
barcodes. The CLI has never called it.

Two limits carry through everything below:

- It returns at most **500 rows at a time**. Ask for more and you get the first 500.
- It accepts at most **5,000 entries** in any one list.

### Where new server-side work goes

#516 is reusable by the CLI because the logic sits in the database, not in the website's JavaScript.

Anything new here follows the same rule: it goes in the database as a shared query, never as CLI-only
Python. The test — could the website use this to do a bulk download with no new backend work? If not,
it is in the wrong place. Otherwise the web needs bulk download later, it gets built a second time,
and the two drift until the same filter returns different results depending on which you used.

## Commands

### Find — what exists

| Command | Answers | Status |
|---|---|---|
| `cyl experiments list` | What experiments exist? | exists |
| `cyl accessions list` | Which accessions are in this experiment? | exists |
| `cyl accessions sample-counts` | How many plants per accession? | exists — **counts the wrong thing** |
| `cyl qc list-sets` | What QC sets exist? | exists |
| `cyl datasets list` / `get` | What trait datasets exist, and what's in one? | exists |

### Query — which plants or scans do I mean

| Command | Answers | Status |
|---|---|---|
| `cyl search` | Which plants match these barcodes / accession / species / experiment? | **new** |
| `cyl traits list` | Which traits exist here, and what range do their values cover? | **new** |
| `cyl traits select` | Which scans have trait X in this range? | **new** |

Nothing today hands you a *set* of plants or scans, and nothing tells you what a trait's values
actually look like. This is the gap.

- `cyl search` can answer in any direction — give it a barcode and get the accession, species and
  experiment; give it a species and get the experiments and accessions inside it. No new view is
  needed: `cyl_plant_search` already carries barcode, accession, species, experiment and wave on
  every row.
- `cyl traits list` reports each trait's observed **minimum and maximum** for the set you are looking
  at, so a range can be chosen from real values rather than guessed. Trait scales differ and values
  may be fractional, so `--trait X:100:400` is unusable until you know what X actually spans.

### Download — fetch it

| Command | Purpose | Status |
|---|---|---|
| `cyl download` | Images + `scans.csv` | exists — **extended to take lists** |
| `cyl download --trait` | Restrict to scans whose trait falls in a range | **new selector** |
| `cyl download --with-traits` | Also write `traits.csv` for those scans | **new flag** |
| `cyl download --traits-only` | Traits without images | **new flag** |
| `cyl download-for-predict` | One scan, for the pipeline | unchanged |
| `cyl batch-download-for-predict` | Many scans, for the pipeline | unchanged |

Selecting and output are separate. `--trait` decides *which* scans; `--with-traits` and
`--traits-only` decide *what files* come out.

### What we are adding

- `cyl search`
- `cyl traits list`
- `cyl traits select`
- `cyl download` — list-valued filters, `--trait`, `--dry-run`, `--yes`, `--with-traits`,
  `--traits-only`
- `cyl accessions sample-counts` — count distinct barcodes

### The pipeline this creates

```
cyl experiments list                              # what's there
cyl search --accession Col-0                      # which plants
cyl traits list --experiment-id 10                # which traits, and what range each covers
cyl traits select --experiment-id 10 \
    --trait primary_root_length:12.4:88.1         # which scans, using a real range
cyl download OUT --experiment-id 10 \
    --trait primary_root_length:12.4:88.1 \
    --with-traits                                 # fetch images + traits
```

Each step emits `qr_code` and `scan_id`, which the next step consumes. The `traits select` step is
optional — `--trait` works on `download` directly, so it is there for when you want to see what you
would get before fetching it.

## The plan for each command

### `cyl accessions sample-counts` — count distinct barcodes

Today it uses `count(*)` over `cyl_plants`. That is a plant-row count, not a barcode count.
`cyl_plants` is unique on `(wave_id, qr_code)`, not on `qr_code`, so a barcode used in three waves is
counted three times. `qr_code` is nullable, so barcode-less rows are counted too. Neither shows in
the output — a row reading `307` gives no way to tell what it is 307 of.

Change the view to `count(DISTINCT qr_code)`, and rename the output column `plant_count` →
`barcode_count`, with the CLI header going `Plants` → `Barcodes`.

The rename is the point. Changing what a number means while leaving its name alone is how someone
ends up comparing an old figure against a new one and trusting the difference. Renaming makes the
change announce itself.

Reported numbers will drop wherever barcodes span waves. The view keeps its existing permissions and
grants; nothing about the table changes. `web/lib/database.types.ts` is regenerated and the
`cyl_accession_sample_counts` assertions in `tests/integration/test_cyl_read_model_views.py` are
updated.

### `cyl search` — select by identity

    cyl search --barcodes-file my200.txt
    cyl search --accession Col-0 --species soybean

Filters stack, each narrowing further. Omit a filter and it doesn't apply.

**Filters stacking is a change.** Today you must pick exactly one of `--experiment-id`, `--scan-id`
or `--experiment-name`, and using two is an error. Stacking matches how #516's query already behaves
— an empty list means "no restriction on this field" — so the CLI hands its filters straight over
with no new database code.

*Rejected:* keeping the one-at-a-time rule and adding a separate `cyl select` command. Two ways to
say the same thing, and `download` still couldn't take a list of barcodes.

**Paging.** The saved query returns 500 rows at a time, so the CLI keeps asking until it has
everything. Taking the first 500 and stopping would give someone asking for 900 scans a silent 500.
That bug already exists in the experiment menus; in a menu it is an annoyance, in a download it hands
someone an incomplete dataset they then analyse. `cyl experiments list` already pages and warns
correctly and is the pattern to copy.

**Batch input.** `--barcodes-file <path|->` reads a file or stdin; `--barcodes a,b,c` for short
lists. Barcodes that match nothing are reported separately from those that matched. More barcodes
than the 5,000 cap is an error naming the cap and the count supplied.

**Give one field, get the others.** `cyl_plant_search` carries barcode, accession, species,
experiment and wave on every row, so any filter can be answered with any of the other fields. No new
view is needed. `--show` picks which:

| You give | You run | You get |
|---|---|---|
| barcodes | `cyl search --barcodes-file x.txt` | plant rows — accession, species, experiment per barcode |
| an accession | `cyl search --accession Col-0 --show experiments` | the experiments it was scanned in |
| an accession | `cyl search --accession Col-0 --show species` | the species it was scanned under |
| a species | `cyl search --species soybean --show experiments` | the experiments in that species |
| a species | `cyl search --species soybean --show accessions` | the accessions in that species |
| an experiment | `cyl search --experiment-id 10 --show accessions` | the accessions in it |

`--show plants` is the default: it is the common identity lookup, and it is the only shape that emits
`scan_id` for the download pipeline.

**The rollup is computed in the database, not the CLI.** `--species arabidopsis` matches tens of
thousands of plants and the saved query returns 500 at a time. Fetching plant rows and deduplicating
them in the CLI would return *the experiments present in the first 500 rows* — a wrong answer that
looks right. `--show` therefore reads a rollup query returning `DISTINCT` values across the whole
match. This is one migration in PR 2; no new view.

That rollup is a **separate function, not a new parameter on `cyl_plant_search_query`.** Adding a
defaulted parameter creates an overload rather than replacing the function, and PostgREST picks an
RPC by the named arguments it is given, so an overload risks ambiguous resolution for the search box
#516 already ships. A separate function leaves the live path untouched.

When `--show plants` would exceed the cap, warn on stderr and name the `--show` value that answers
the question without truncating.

This follows what #516 already does on the web, where a species match surfaces the species rather
than its thousands of plants, and a barcode match surfaces the plants with species and experiment as
context.

**Output.** `--output csv|json`. At `--show plants` the columns include `qr_code` and `scan_id`; at
the other values, the distinct ids and names for that field. Menus and warnings go to stderr so
stdout stays machine-readable.

### `cyl download` — take lists

    cyl download OUT --barcodes-file my200.txt
    cyl download OUT --species soybean --accession Col-0
    cyl download OUT --experiment-id 10 --experiment-id 22

`fetch_scans()` moves from a single `experiment_id` and a single `qr_code` to list-valued filters.
`--experiment-id` and `--scan-id` become repeatable. `--barcodes-file`, `--barcodes`, `--accession`
and `--species` are added. `--experiment-name` keeps its current behaviour and its ambiguity guard.

**`--dry-run`** resolves the selection, reports scan count, experiment count and estimated image
count, then exits without downloading.

**`--yes`** is required above a threshold number of scans. Below it, downloads run unprompted.

Once one flag can select thousands of images, a typo gets expensive. An "are you sure?" prompt was
rejected because it breaks scripting, which is the point of a CLI.

**`--trait NAME:MIN:MAX`** restricts the download to scans whose trait falls in a range, repeatable
and narrowing:

    cyl download OUT --experiment-id 10 \
        --trait primary_root_length:12.4:88.1 --with-traits

It is the same predicate `cyl traits select` uses, sent to the same query, so no second code path
exists. Run `cyl traits list --experiment-id 10` first to see what the trait actually spans.

**Selecting is not the same as output.** `--trait` decides *which* scans are downloaded.
`--with-traits` and `--traits-only` decide *what files* are written. They are independent: you can
select by trait and take images only, or take traits alongside images without filtering by any trait
value.

### `cyl traits list` — what traits exist, and what range they cover

    cyl traits list --experiment-id 10
    cyl traits list --species soybean --output csv

    trait                  n_scans   min     max
    primary_root_length      1240   12.4   883.1
    total_length             1240   40.2  2104.7
    solidity                 1198    0.31     0.98

A trait range cannot be chosen without knowing what the trait spans. Scales differ between traits,
values may be fractional, and nothing in the CLI tells you today — `cyl datasets get` shows trait
*names* for one dataset and no values at all. Passing `--trait primary_root_length:100:400` is a
guess until this exists.

**It takes the same filters as everything else**, so the range describes the set you care about
rather than the whole database. The minimum and maximum for one experiment are the useful numbers;
the global ones usually are not.

**Ranges are computed in the database.** Aggregating client-side would mean fetching every trait
value, which hits the row cap and returns the range of the first page — a wrong answer that looks
right. The aggregate goes in the shared query, so the website can use it too.

Trait names are listed from the existing `cyl_scan_trait_names` view, which already carries the
distinct names in latest-source data and is already granted to the read roles. Scoping that listing
to an experiment is the part that needs the new query.

`get_experiment_traits(experiment_id_, source_id_, run_id_)` also already exists and returns per-scan
trait rows for one experiment — `scan_id`, `plant_qr_code`, `accession_name`, `trait_name`,
`source_id`, `trait_value` — as `SECURITY INVOKER`, granted to the read roles, and unused by the CLI.

It is not widened to take the shared filters. Adding parameters to an existing function overloads it
rather than replacing it, and it carries integration tests asserting row-for-row parity with
`get_scan_traits` along with its EXECUTE grants. The new queries return the **same column shape**, so
the two agree and nobody has to reconcile two trait row formats, while `get_experiment_traits` keeps
serving its existing per-experiment contract unchanged.

A name matching several sources is reported per source rather than merged, so two different
measurements are never averaged into one range.

### `cyl traits select` — select by measurement

    cyl traits select --barcodes-file my200.txt --trait primary_root_length:100:400

Of these 200 barcodes, which have primary root length between 100 and 400.

**It takes the same filters as `cyl search`,** applied first, with the trait test run inside that
set. Filters and trait ranges go to the database in one request rather than the CLI intersecting two
result sets — one round trip, caps enforced server-side, and the website can reuse it.

**Ranges are written as one token.** `--trait NAME --min X --max Y` repeated is ambiguous: with two
traits and two `--min` flags, nothing says which bound belongs to which trait. So the bounds travel
with the name:

    --trait primary_root_length:100:400    # both bounds
    --trait total_length:50:               # lower only
    --trait solidity::0.8                  # upper only

Repeatable; multiple ranges narrow further. `--min` / `--max` stay as an alternative for a single
range and are rejected when more than one `--trait` is given.

**You choose scans or barcodes.** A trait value belongs to a scan, and a plant is scanned many times
as it grows, so "barcodes where the root is 100–400 long" is ambiguous — a plant can measure 80 on
day 3 and 250 on day 10.

| `--grain`        | One row per      | Columns                                             | For                     |
| ------------------ | ---------------- | --------------------------------------------------- | ----------------------- |
| `scan` (default) | matching scan    | `qr_code`, `scan_id`, `plant_age_days`, value | feeding`cyl download` |
| `barcode`        | matching barcode | `qr_code`, `scans_matched`, `scans_total`     | shortlisting plants     |

At `barcode` grain, `--match any|all` decides whether one qualifying scan is enough (`any`, default)
or all must qualify. `--match all` will often return nothing for a growth trait, since a plant
measured on day 3 and day 30 is rarely in one range throughout — the help text says so.

`scan` is the default because that is the level the measurement exists at, it loses nothing, and it
emits `scan_id` so the download pipeline works without a flag. Barcode grain reports `scans_matched`
next to `scans_total`, so a plant qualifying on 1 of 12 scans looks different from one qualifying on
all 12.

**A trait name matching several sources** is an error listing the candidates, not a silent pick.

**It prints ids rather than downloading,** keeping selection and fetching composable and avoiding a
second copy of the download logic to keep in step.

This needs a new saved query — "scans whose trait X is between A and B" — built like
`cyl_plant_search_query` so the website can call it too.

### `cyl download --with-traits` / `--traits-only`

`--with-traits` writes `traits.csv` next to `scans.csv` for the same resolved scans, read through the
existing source-aware trait views and keyed on `scan_id`, so the two files join without manual
correlation. Scans with no trait rows still appear in `scans.csv`, and the count of them is reported.

`--traits-only` writes both CSVs and skips images. It conflicts with `--meta-only`.

## How this ships

One command per PR.

| PR | Command | Depends on |
|---|---|---|
| 1 | `cyl accessions sample-counts` counts distinct barcodes | — |
| 2 | `cyl search` | — |
| 3 | `cyl download` takes lists | 2 |
| 4 | `cyl traits list` | 2 |
| 5 | `cyl traits select`, and `--trait` on `cyl download` | 3, 4 |
| 6 | `cyl download --with-traits` / `--traits-only` | 5 |

**PR 1** is a view redefinition plus tests. Nothing depends on it, so it can go first and alone.

**PR 2** builds the piece that turns filters into a set of plants by calling #516's query, and puts
`search` on top. Everything later reuses that piece. It touches no existing command, and it settles
early whether #516's query carries what the CLI needs.

**PR 3** carries the only breaking change — filters stacking instead of excluding — so it stays on
its own.

**PR 4** adds the trait aggregate: names, counts, minimum and maximum for the current filter. It
comes before `traits select` because a range cannot be chosen until you can see one. It depends on
PR 2 for the filters, not on PR 3, so it can be reviewed in parallel.

**PR 5** adds the range predicate to both `traits select` and `download`, using the one query built
here. Keeping them together means the predicate is defined once and cannot drift between the two
commands.

**PR 6** is worth having only once selection by trait exists.

After PR 6, giving the website bulk download is a front-end job, because every query it needs is
already shared. That is a separate change.
