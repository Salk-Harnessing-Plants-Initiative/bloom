# bloomctl

Python command-line tool for the Bloom server — download cylinder experiments
(metadata + images), write per-scan pipeline results back, and manage
credentials. Successor to the Node `@salk-hpi/bloom-cli`. Tracked by issue #347.

## Container image

`bloomctl` is also published as a container image, for use as a step in
pipelines (e.g. sleap-roots-pipeline's Argo DAG) rather than a `pip install`:

```
ghcr.io/salk-harnessing-plants-initiative/bloomctl
```

- `sha-<short-git-sha>` — immutable, pushed on every commit to `staging` that
  touches `bloomcli/**`.
- `staging` — mutable, points at the most recent `staging` build.
- `<version>` (e.g. `0.1.0a2`) — pushed when a matching GitHub Release is
  published; guaranteed to match the PyPI-published version of the same name.

The image is built directly from this repo's source at the commit being
built (not from PyPI), so it's always available immediately after a
`staging` push regardless of PyPI release timing — see
`.github/workflows/docker-build-bloomcli.yml`. Every PR touching
`bloomcli/**` builds and Trivy-scans the Dockerfile via `pr-checks.yml`'s
`docker-build` job (the same pre-merge gate every other Bloom image gets);
the publishing workflow itself only builds and pushes, it never runs on a
pull request.

Provenance: `docker/metadata-action` bakes standard OCI labels into every
image, including `org.opencontainers.image.revision` (the full source
commit SHA) — recoverable from a running/pulled image with no other
context via `docker inspect <image> | jq .Config.Labels`.

```
docker run --rm ghcr.io/salk-harnessing-plants-initiative/bloomctl:staging \
  cyl ingest-result path/to/scan.result.json
```

## Commands

`login` is flat; assay-specific commands are grouped by data type (`cyl`,
`genome`, `plate`, `scrna`). Each command is tagged **[read]** or **[write]** — see
[Access & roles](#access--roles).

- `bloomctl login` — bootstrap client config from the Bloom server and store
  credentials per profile.
- **[write]** `bloomctl genome upload <name> --fasta … --gtf …` — store a reference
  genome's FASTA and GTF as its next version, for the Cell Ranger workflow. See
  [below](#bloomctl-genome-upload).
- **[read]** `bloomctl genome list` / `bloomctl genome download <name>[.vN]` — see which
  genomes and versions Bloom holds, and fetch a ready version's FASTA and GTF, checked
  against Bloom's record. See [below](#bloomctl-genome-list-and-download).
- **[read]** `bloomctl cyl download <out_dir> …` — download a cylinder experiment
  or single scan (metadata `scans.csv` + per-frame images). Select the experiment
  by `--experiment-id N`, `--scan-id N`, or `--experiment-name "<text>"` (a
  case-insensitive substring search on the experiment name, run server-side and
  index-backed; `--species` to narrow; an ambiguous name lists the candidates and
  exits without downloading).
- **[read]** `bloomctl plate download <out_dir> …` — download a plate (GraviScan)
  experiment or single scan (metadata `plates.csv` + per-plate images). Same
  selectors as `cyl download`; narrow with `--plate-id`, `--wave-number` or
  `--session-id`.
- **[read]** `bloomctl cyl download-for-predict <scan-id> <out>` — stage one scan
  into the predict-ready layout (see below); produces a **different** output tree
  than `cyl download` — use this only for A4 pipeline stage-in.
- **[read]** `bloomctl cyl batch-download-for-predict <out_dir> …` — stage a
  batch of scans into the predict-ready layout in one invocation (see below);
  the batch sibling of `download-for-predict`, for the A4 per-batch pipeline.
- **[write]** `bloomctl cyl ingest-result <envelope>` — write a per-scan pipeline
  `ResultEnvelope` back to Bloom (see below).
- **[write]** `bloomctl cyl batch-ingest-result <envelopes_dir>` — write back a
  batch of per-scan `ResultEnvelope`s in one invocation (see below); the batch
  sibling of `ingest-result`, for the A4 per-batch pipeline.
- **[write]** `bloomctl cyl create-test-scan --poison | --good --frames-dir <dir>` —
  developer tool: create one synthetic cylinder scan in the staging test
  experiment `A4-PIPELINE-E2E-TEST` (`experiment_id 12880747`) only. `--poison`
  makes a scan that will fail to download; `--good` makes a real, downloadable
  one from frame images you supply (e.g. via `cyl download`). One scan per
  invocation — run it again for another. Requires a write-capable profile (e.g.
  `staging-writer`); `pipeline-staging` lacks the grants it needs.
- **[read]** `bloomctl cyl datasets list` — list cylinder trait datasets (all by
  default). Scope to one experiment with `--experiment-id N` (scriptable) or
  `--experiment` to **pick one from a menu** (needs a terminal). `--output csv|json`
  for machine-readable output.
- **[read]** `bloomctl cyl datasets get <name>` — show one dataset's details and the
  unique traits it contains, via the `cyl_dataset_trait_names` view (`--json` output).
- **[write]** `bloomctl cyl datasets create <name> <experiment_id> <trait_source_name>` —
  create a trait dataset (`--qc-set-name` to exclude a QC set, `--timepoints`). For pipeline
  data a source name selects **one scan's** rows (write-back stores one source per scan);
  a dataset built from a recipe across many scans needs `create_cyl_dataset`'s recipe mode,
  which bloomctl does not expose yet (#481).
- **[read]** `bloomctl cyl experiments list` — list cylinder experiments (species,
  name, id), sorted by species then name. Filter with `--species NAME` (scriptable) or
  `--species-menu` to **pick a species from a menu** (needs a terminal). Choose the output
  with `--output csv|json` (default is the table; `--json` is an alias for
  `--output json`) — handy for grabbing an `experiment_id` for `cyl download`;
  `--limit` caps the fetch.
- **[read]** `bloomctl cyl accessions list` — list the accessions used in an
  experiment. Pass `--experiment-id N` (scriptable), or omit it to **pick an
  experiment from a menu** (needs a terminal). `--output csv|json` for
  machine-readable output.
- **[read]** `bloomctl cyl accessions sample-counts` — plant count per accession, per
  species. Filter with `--species NAME` (scriptable) or `--species-menu` to **pick a
  species from a menu** (needs a terminal); omit both for all species. `--output csv|json`
  for machine-readable output.
- **[read]** `bloomctl cyl qc list-sets` — list cylinder QC sets (name, species,
  experiment, number of QC codes). Prints a table by default; `--output csv|json`
  for machine-readable output.
- **[write]** `bloomctl scrna hdf5 upload <file.h5ad> --name … --species … --annotation …`
  — store a single-cell dataset's AnnData file, gzipped and named by its SHA-256, and load
  its cells into Bloom, after checking both and showing what will happen; asks first
  (`--yes` in scripts, `--dry-run` to check only). See below.
- **[read]** `bloomctl scrna hdf5 download <dataset>` — fetch a dataset's AnnData file,
  by name, id or `--checksum`, checked against its fingerprint (see below).
- **[read]** `bloomctl scrna hdf5 list [search]` — the dataset files storage holds, each
  with its size and the dataset that points at it; `--file` says whether one local
  file is already stored. `--output csv|json` for machine-readable output.

Run `bloomctl <command> --help` for the full option list of any command.

## Common conventions

These apply across the `cyl` commands, so the per-command sections below stay short:

- **Profiles** — every command takes `-p/--profile <name>` (default `prod`). `bloomctl login`
  writes a profile; use separate profiles to keep prod / staging / local logins side by side
  (`bloomctl login --server https://staging.bloom.salk.edu -p staging`, then `… -p staging` on
  any command). Staging is for the Bloom team only. `-p` only names the saved login: without
  `--server`, `login` signs in to prod whatever the profile is called.
- **Machine-readable output** — the `list` commands take `--output csv|json` (with `--json` as a
  back-compat alias for `--output json`); the default is a human table. Pipe it: e.g.
  `cyl experiments list --output json | jq '.[].experiment_id'`.
- **Species selector** — commands that filter by species take `--species <common-name>` (a typed,
  scriptable value) or `--species-menu` to pick from a numbered menu; the two are mutually exclusive,
  and omitting both means all species. `--species` is **always a value**, `--species-menu` is
  **always the picker** — the same everywhere (`experiments list`, `accessions sample-counts`, and
  the typed `--species` narrower on `download --experiment-name`).
- **Interactive pickers** — the menu flags (`--species-menu`, and `--experiment` on `datasets list`)
  print a numbered menu to **stderr** (so `--output` on stdout stays clean) and need a terminal. In
  a pipe/CI there's no one to answer, so they **abort rather than guess** — for scripting, pass the
  typed value/id instead.
- **Read vs write** — see [Access & roles](#access--roles). Browsing/downloading works for any
  logged-in user; the **[write]** commands need an account with write access.

## Worked example: from login to a downloaded experiment

A start-to-finish walkthrough for the common task — _"get me the images + metadata for the soybean
drought experiment."_

```bash
# 1. Log in (once). Defaults to prod; use --server + -p for a named staging/local profile.
bloomctl login
#    → prompts for email + password; writes credentials to ~/.bloom

# 2. Find the experiment — browse by species from a menu (menu prints to stderr):
bloomctl cyl experiments list --species-menu
#    Select a species:
#      0) All species
#      1) Arabidopsis
#      2) Soybean
#    → prints the table; note the experiment_id you want, e.g. 42
#    (or skip this and let `download` resolve the name — see step 4)

# 3. (optional) Sanity-check the contents before pulling gigabytes of images:
bloomctl cyl accessions list --experiment-id 42       # which accessions are in it
bloomctl cyl accessions sample-counts --species-menu  # plant count per accession (pick a species)
bloomctl cyl datasets list --experiment-id 42         # any trait datasets already built

# 4. Download it — metadata only first to preview, then the full pull:
bloomctl cyl download ./soy-drought --experiment-id 42 --meta-only   # scans.csv only
bloomctl cyl download ./soy-drought --experiment-id 42               # scans.csv + all frames
#    …or without ever looking up the id:
bloomctl cyl download ./soy-drought --experiment-name "drought" --species Soybean
```

Result: `./soy-drought/scans.csv` (one row per scan) plus `./soy-drought/images/Wave{n}/…` (the
frames). Hand that directory to your analysis — or use `cyl download-for-predict` to stage scans for
the SLEAP-roots pipeline (below). For scripting, swap the menus for explicit ids and add
`--output json` (see [Common conventions](#common-conventions)).

## `bloomctl cyl download`

Download a whole experiment (or a single scan): the metadata table `scans.csv` plus every frame
image, into `<out_dir>`.

```
bloomctl cyl download <out_dir>
  ( --experiment-id N | --scan-id N | --experiment-name "<text>" [--species <name>] )
  [--meta-only] [--plant-qr-code QR] [--plant-age-min D] [--plant-age-max D]
  [--limit N] [-n/--workers N] [-p/--profile PROFILE]
```

Pick **exactly one** target:

- `--experiment-id N` — download the whole experiment by id (the scriptable path).
- `--scan-id N` — download a single scan.
- `--experiment-name "<text>"` — resolve the experiment **by name** (a case-insensitive substring
  match, run server-side) and download it. `--species <name>` narrows when a name is ambiguous.
  - one match → downloads it (prints `Matched: <name> (id N)` to stderr);
  - several matches → lists the candidates (id · name · species · created) and **exits without
    downloading** — so a pipeline never fetches the wrong experiment;
  - no match → a clear error.

Other options: `--meta-only` (write `scans.csv`, skip images), `--plant-qr-code` (one plant),
`--plant-age-min/--plant-age-max` (age window in days, default 0–1000), `--limit` (sample at
most this many scans, default 100000 — see below).

```bash
# by id, or just by name
bloomctl cyl download ./out --experiment-id 42
bloomctl cyl download ./out --experiment-name "drought 2024"

# narrow an ambiguous name to one species; metadata only
bloomctl cyl download ./out --experiment-name soy --species Soybean --meta-only

# a single scan
bloomctl cyl download ./out --scan-id 9001
```

### Download speed (`--workers`)

Frames download concurrently — 8 at a time by default, which is what makes a large
experiment finish in a sensible amount of time. Tune it with `-n`/`--workers`:

```bash
bloomctl cyl download ./out --experiment-id 42 --workers 16   # faster on a good connection
bloomctl cyl download ./out --experiment-id 42 --workers 1    # sequential
```

Valid range is 1–64; the ceiling is a guard against pointing an unbounded number of
connections at the server. If downloads start erroring on a flaky connection, lower it.

### Watching a long download

A large experiment takes hours, so the command reports how far it has got — roughly every five
seconds, on stderr:

```
Wrote 5750 scans -> ./out/scans.csv
  Listing frames: 5,750/5,750 scans (100%)
  12,480/413,926 frames (3%)
  25,910/413,926 frames (6%)
```

If the count stops moving, the download is stuck rather than slow. Progress goes to stderr, so
piping stdout somewhere still gives you just the paths and the final summary.

### Resuming an interrupted download

A download is **resumable**: frames already in `<out_dir>` are kept and counted as
`already on disk`, so re-running the same command fetches only what is still missing. That
applies to any interruption — a failed run, `Ctrl-C`, a dropped network, a closed laptop:

```bash
bloomctl cyl download ./out --experiment-id 42   # interrupted at 117k of 414k frames
bloomctl cyl download ./out --experiment-id 42   # picks up from 117k
```

**One selection per directory.** A download records what it fetched, so re-running the same
command resumes it. Downloading something _different_ into the same directory is refused —
otherwise you would end up with two sets of images in one tree and a `scans.csv` describing only
the newer one. So a spot-check and a full pull want separate directories:

```bash
bloomctl cyl download ./check --experiment-id 42 --plant-qr-code QR-1   # one plant
bloomctl cyl download ./full  --experiment-id 42                        # everything
```

**And one scan method per directory.** `cyl download` and `plate download` refuse each other's
directories, reported as `method was 'cyl', now 'plate'`. The two record different things — a
`scans.csv` against a `plates.csv`, frames against captures — and their ids are separate
sequences, so the same number means different rows. Give each method its own directory.

> **A directory written by bloomctl 0.1.0a5 or earlier carries no method.** It reads as a
> `cyl download`, which is what it can only have been, so those directories keep resuming
> under `cyl` and are refused by `plate`.

> **If the directory was written by bloomctl 0.1.0a3 or earlier**, download the experiment again
> into a new directory. Those releases wrote frames non-atomically, so an interrupted run could
> leave a truncated file behind, and resume treats any non-empty file as complete.

Long runs keep working past the point where the login's token is refreshed, so a download that
takes several hours completes. If a storage error does mention an expired session, re-running the
command resumes from wherever it stopped.

> `--experiment-name` requires the `cyl_experiment_search` DB function (migration
> `…_add_cyl_experiment_search.sql`) to be applied on the server you're pointed at.

## `bloomctl plate download`

Download a whole plate (GraviScan) experiment, or a single plate scan: the metadata table
`plates.csv` plus every plate image, into `<out_dir>`.

```
bloomctl plate download <out_dir>
  ( --experiment-id N | --scan-id N | --experiment-name "<text>" [--species <name>] )
  [--meta-only] [--plate-id ID] [--wave-number N] [--session-id N]
  [--limit N] [-n/--workers N] [-p/--profile PROFILE]
```

Target selection works exactly like `cyl download` — one of `--experiment-id`, `--scan-id`, or
`--experiment-name`, with `--species` to narrow an ambiguous name. One difference: the candidate
list also prints each experiment's **rig** (`system_name`), because a plate experiment is unique
on _(species, name, system)_ — the same name really can exist on two GraviScan rigs, and without
the rig the two rows would look identical.

Narrowing options: `--plate-id` (one plate barcode), `--wave-number` (one wave), `--session-id`
(one continuous run of cycles), `--limit` (sample at most this many scans, default 100000).

`--limit` is for looking at a sample of an experiment before committing to the whole thing.
It is not a way to export one in parts: the limit is part of what a directory records as its
selection, so a sample and a full download belong in separate directories. Samples are taken
in scan-id order, so the same limit gives the same captures every time.

A run that comes back with exactly as many scans as the limit warns that the newest captures
were dropped. It cannot tell that case from an experiment holding exactly that many scans, so
the warning is on the cautious side: if the count is one you recognise as the whole experiment,
nothing is missing. Raising `--limit` needs a new directory, since the limit is part of the
selection this one recorded.

```bash
# a whole experiment, or just one plate from it
bloomctl plate download ./gravi --experiment-id 12
bloomctl plate download ./gravi --experiment-id 12 --plate-id PLATE-001

# one session's cycles; metadata only first
bloomctl plate download ./gravi --experiment-id 12 --session-id 88 --meta-only
```

### What lands on disk

A cylinder scan holds many frames of one plant at one moment. A plate scan holds exactly **one**
image — repetition comes from _time_ instead, because a continuous session captures the same
plate once per cycle. So the tree groups by plate, and names each file by its capture:

```
out/
  plates.csv                 one row per scan, with an image_path column
  plate_sections.csv         one row per (section, plant QR) — only if the plates carry metadata
  download_log.txt           one line per capture, plus a summary
  .bloomctl-download.json    which selection and scan method this directory holds
  images/
    Wave3/
      PLATE-001/
        2026-05-27T14-03-11+00-00_c0000.jpg
        2026-05-27T14-13-11+00-00_c0001.jpg
```

The capture instant leads the filename, so lexical order is chronological order — a
gravitropism time series reads correctly straight off a directory listing, and through
ffmpeg's glob or ImageJ's image sequence import. The cycle trails it for readability and is
dropped for single-mode scans, which have none. It cannot lead: `cycle_number` restarts at 1
for every session, and a plate directory spans sessions, so ordering by cycle interleaves two
time courses.

`plate_sections.csv` is separate rather than more columns on `plates.csv`: a plate's sections are
one-to-many and their plant QR codes one-to-many again, so folding them in would mean duplicating
every scan row. Join it back on `metadata_id`.

### Resume

Same as `cyl download` — re-running the same command into the same directory picks up where it
stopped, and a _different_ selection in that directory is refused rather than mixed in.

Plate resume is slightly stronger: `gravi_images` records `file_size_bytes`, so a file is only
skipped when its size matches what the database says. A truncated file is fetched again instead
of being treated as complete forever. If the recorded size is itself wrong, the download still
succeeds and the log carries a `note=` saying so — otherwise that object would be re-fetched on
every run with no explanation.

## Finding what to download

The read commands help you go from "which experiment?" to an id you can feed `cyl download`:

```bash
# browse experiments; pick a species from a menu, or list all as JSON
bloomctl cyl experiments list --species-menu
bloomctl cyl experiments list --output json | jq -r '.[] | "\(.experiment_id)\t\(.experiment)"'

# what accessions / how many plants are in an experiment
bloomctl cyl accessions list --experiment-id 42
bloomctl cyl accessions sample-counts --species-menu     # pick a species from the menu
bloomctl cyl accessions sample-counts --species Canola    # or filter directly (scriptable)

# trait datasets and QC sets
bloomctl cyl datasets list --experiment                  # pick the experiment from the menu
bloomctl cyl datasets get canola-v1
bloomctl cyl qc list-sets --output csv
```

- `experiments list` — species · name · id, sorted; `--limit` caps the fetch (default/max 1000 and
  warns on stderr if it's hit).
- `accessions list` — accessions used in one experiment; hybrid selector (`--experiment-id`, or a
  menu when omitted).
- `accessions sample-counts` — plant count per accession per species, **pooled across all
  experiments** (a total headcount, not a per-experiment replicate count).
- `datasets list` / `get` — trait datasets (filter by `--experiment`); `get <name>` shows one
  dataset's details and the unique traits it contains.
- `qc list-sets` — QC sets for live experiments; `--include-deleted` also shows sets on
  soft-deleted experiments.

(`cyl ingest-result`, `cyl download-for-predict`, and their batch siblings are documented in full
below.)

## `bloomctl cyl download-for-predict`

Stage one cylinder scan into the layout the warm-predict container expects.
Unlike `cyl download` (which writes `images/Wave{n}/…` + `scans.csv`), this
command co-locates frames with a `scan_metadata.json` sidecar so
`sleap_roots_predict.discover_scans` can find them — use it for A4 per-scan
pipeline stage-in, not as a replacement for `cyl download`.

```
bloomctl cyl download-for-predict <scan-id> <out>   [-p/--profile PROFILE] [-n/--workers N]
```

- Writes frames to `<out>/scan_<scan_id>/<frame_number><ext>`, up to `--workers` frames at once
  (1-64, default 8, `1` = sequential — same range/default as `cyl download`'s own `--workers`).
- Authors `<out>/scan_<scan_id>/scan_<scan_id>.scan_metadata.json` with:
  - `scan_key` — `scan_<scan_id>` (matches the filename stem).
  - `params` — `{species, mode, age}`, resolved via `sleap-roots-contracts`
    (`mode` is always `"cylinder"`).
  - `image_ids` — real `cyl_images.id` values, required for the write-back RPC
    to resolve the scan (see rationale in design.md / bloom#411).
  - `images_checksum` — `sha256:<hex>` over the downloaded frame bytes.
- Exits non-zero with a readable message if the scan isn't found, has no
  frames, or any frame fails to download — on a frame-download failure, no
  sidecar is written (successfully-downloaded frames remain on disk).
- A successful re-run reconciles away any stray frame file left by an earlier
  failed attempt, so the directory always matches the written sidecar exactly.

Auth: same saved login profile as other `cyl` commands.

Example:

```
bloomctl cyl download-for-predict 1 ./staged
```

## `bloomctl cyl batch-download-for-predict`

Stage a batch of cylinder scans into the predict-ready layout in one
invocation — the batch sibling of `download-for-predict`, for the A4
per-batch pipeline's `download-all` Argo task.

```
bloomctl cyl batch-download-for-predict <out_dir>
  (--scan-ids-file <scan_ids.json | -> | --scan-ids 1,2,3)
  [-p/--profile PROFILE] [--json] [--lock-staleness-seconds N] [-n/--workers N]
```

- Exactly one of `--scan-ids-file` (a JSON array of integer scan_ids, read from
  a path or stdin when the value is `-`) or `--scan-ids` (a comma-separated
  list, for ad hoc manual use) is required.
- Stages every `scan_id` into `<out_dir>/scan_<scan_id>/`, identical to what
  `download-for-predict` writes for one scan — each scan's frames download up to `--workers`
  at once (1-64, default 8, `1` = sequential), same as the single-scan command; scans
  themselves are still staged one at a time.
- **Isolates per-scan failures** — one bad scan (not found, no frames, a
  metadata-resolution failure, a partial frame-download failure, or lock
  contention with another live invocation) is recorded and reported, but does
  not abort the rest of the batch.
- **Skips an already-staged scan** — if `<out_dir>/scan_<scan_id>/` already has
  a valid sidecar (parses, `scan_key` matches), that scan is reported
  `skipped` and not re-downloaded.
- **Writes a `RunManifest`** — after every scan is processed, writes a
  `sleap_roots_contracts.RunManifest` recording every usable (`ok` or
  `skipped`) `scan_key` *this invocation* found. The file is named per run:
  `<out_dir>/run_manifest.<ARGO_WORKFLOW_NAME>.json` inside Argo (whitespace
  stripped), and `<out_dir>/run_manifest.json` when `ARGO_WORKFLOW_NAME` is
  unset or blank. Its `pipeline_run_id` is that same run id, or a generated
  `local-<8 hex chars>` placeholder outside Argo (stamped inside the file,
  never used to name it). The write **replaces** any existing file of that
  name — it never merges with an earlier manifest, so running twice into the
  same `out_dir` without `ARGO_WORKFLOW_NAME` keeps only the second run's keys,
  and a legacy `run_manifest.json` next to a per-run file is never touched. A
  downstream consumer reads this to know which scans in `<out_dir>` are this
  run's to process (bloom #934).
- **Locks against concurrent invocations** — a per-scan lock
  (`<out_dir>/.locks/{scan_key}.lock`) guards each scan's skip-check through
  its sidecar write, and a separate lock (`<out_dir>/.locks/manifest.lock`)
  guards the manifest write (one lock per `out_dir`, whatever the manifest's
  name), so two invocations targeting the same
  `out_dir` can't corrupt each other. `--lock-staleness-seconds` (default
  `900`) controls how old an abandoned lock must be before it's reclaimed
  rather than treated as still held.
- `--json` prints one entry per scan_id (`scan_key`, `status`, `error`) as a
  JSON array; without it, a human-readable summary plus one line per failure.
- **Exit code:** `0` if every scan succeeded, was skipped, or the input was
  empty; `3` if at least one scan failed — whether that's one scan out of many
  or every scan in the batch, since `3` only means "not every scan succeeded,"
  never "some scan did" (mirrors `sleap_roots_predict`/`trait_extractor`'s own
  `0`/`3` convention, bloom #772). Check the written manifest or `--json`
  output to see which scans, if any, actually staged. A usage error exits
  `2`; a manifest-lock/write failure, or an `ARGO_WORKFLOW_NAME` that cannot
  name a manifest file (checked before any scan is staged), exits `1` —
  independent of any scan's outcome. Empty input still exits `0`.

Auth: same saved login profile as other `cyl` commands.

Example:

```
bloomctl cyl batch-download-for-predict ./staged --scan-ids-file scan_ids.json
```

## `bloomctl scrna hdf5 upload` / `download` / `list`

A single-cell dataset's whole AnnData file (`.h5ad`) is kept in the `scrna`
bucket's `h5ad/` folder, gzipped as it is and named by the SHA-256 of the
uncompressed file. The bucket holds other kinds of object besides — per-gene
counts above all — so these commands sit under the form they act on, `hdf5`. That SHA-256 is the dataset's `source_checksum`, so a dataset
finds its file with no lookup table, and the same file uploaded twice is one
object.

```bash
bloomctl scrna hdf5 upload my_dataset.h5ad --name "My dataset" \
  --species Arabidopsis --annotation cell_type --create -p staging    # store and load
bloomctl scrna hdf5 download "MYB41 transgene" -p staging            # → MYB41_transgene.h5ad
bloomctl scrna hdf5 download 14 --out myb41.h5ad -p staging          # by id
bloomctl scrna hdf5 download --checksum 82278a…a54f -p staging       # by fingerprint
bloomctl scrna hdf5 list -p staging                                  # what is stored
bloomctl scrna hdf5 list myb41 -p staging                            # by dataset name
bloomctl scrna hdf5 list --file myb41_transgene_load.h5ad -p staging # is this one stored?
```

**Upload** stores the file and loads the dataset from it — its cells and each gene's counts —
so the explorer can show them and colour them by any gene. It needs a writer or admin login, or the pipeline's (`bloom_workflows`).
Before sending anything it checks the file's structure:

- every cell has an ID and none repeats (a barcode shared across samples cannot
  be the index); the same for genes, in whatever form the species' annotation
  writes them
- `X` holds only finite values
- the UMAP (`obsm['X_umap']`, or the array `--umap-key` names) is there, has two columns
  and a row per cell, holds only finite coordinates small
  enough for the explorer to store, and does not pile more than a thousandth of the cells on
  a single point — an array allocated and never filled passes every other check and draws the
  whole dataset as one dot. These two limits are the loader's own, so a UMAP this accepts is a
  UMAP the loader accepts; the loader checks more besides, so passing here is not a promise
  that the load will succeed
- the file's data is in the file: an external link, or a virtual dataset whose values live in
  another file, is refused, and so is a link that stands in for an array rather than the
  array itself — each would store an object that reads differently on every machine, and
  would let a refusal quote a file nobody handed in
- `layers['counts']`, when present, matches `X`'s shape and holds no negative value
- `uns['normalization']` says how `X` was made:
  `transform` (`log1p`, `log2p`, `none`), `scaling` (`library_size`, `none`,
  `other`), `target_sum` for `library_size`, a `description` for `other`, and
  optionally `counts_layer`. A file a dataset was loaded from before this existed
  is accepted without the block when that dataset records it.

A file with no UMAP is refused: the explorer plots stored coordinates and never computes
them. A file with no `obsm['X_umap']` but another array with two columns and a row per cell
is refused naming it; pass `--umap-key NAME` if that array is the UMAP.

Next it reads the cells and the gene names, the way the load will. Only the cell table
(`obs`), the UMAP, the gene table (`var`) and the matrix's shape are read — no expression
matrix or layer, except the genes `--expect-nonzero` names — so the memory this takes does
not grow with the size of the data:

- `--annotation` names the obs column holding the cell-type or cluster label assigned to
  each cell — annotated types such as `Cortex`, or cluster ids such as Leiden's `0`, `1`, … —
  which the map colours by (at most 23 labels, one colour each); `--sample-column` (default
  `sample`) names the column holding each cell's sample
- every cell has a barcode, a type and a sample; a blank, or a value that reads as missing
  (`nan`, `None`, …), is refused rather than stored as a cell type
- `--expect-cells N` refuses a file holding any other number of cells
- sample names are at most 100 characters, the most the database holds
- gene names lose an `.Araport11.N` suffix, and then have to be unique and usable in an
  object path (no `/`, no `..`)
- `--expect-nonzero GENE=COUNT` (repeatable) refuses the file unless that gene is non-zero in
  exactly that many cells — a way to pin a gene whose count is known independently, such as
  a transgene's
- the file does not change while it is read; one re-saved part-way is refused. The same check
  runs again while the gene counts are read, after the file is stored: if the file changes
  then, the load stops with nothing from the changed file written, and either the original
  file is put back and the same command continues it, or the new file is uploaded as a new
  dataset with another `--name` and `--create`

Then it gzips the file, which also fingerprints it, and checks what needs that: the gzipped
size is within 500 MB, and a file without `uns['normalization']` is one a dataset already
records the normalization for. Last, it looks the dataset up by `--name` within `--species`
(a common name, matched ignoring case and surrounding spaces) and decides what the load
will do. Surrounding spaces are ignored in dataset names, and a name that differs from an existing
dataset's only in capital letters is refused, asking for the exact name or a new one, so
`myb41` cannot quietly stand in for, or sit beside, `MYB41`:

- **no such dataset**: registered, but only with `--create`, so a mistyped name is refused
  rather than loaded as a second copy
- **a load from this file that stopped**: continued from what is stored, given the same
  options it was started with
- **finished from this file without its gene counts** (loaded before the upload wrote them):
  the stored cells have to be this file's, in its order, and then the missing counts are
  added
- **finished from this file**: nothing to load — "already loaded". Given a label option or an
  `--annotation` the dataset was not loaded with, it is refused instead, pointing to
  `--add-labels`, rather than reporting success and writing nothing
- **finished from another file**, or a dataset that records no file: refused. A loaded
  dataset is not replaced; the refusal suggests a new name to load the file under, such as
  `MYB41_v2`

Only once every check has passed does it show what the file holds and what the load will
do, and ask. For the MYB41 file on staging, already loaded as dataset 14:

```text
myb41_transgene_load.h5ad
  cells          8,683
  genes          27,656
  UMAP           obsm['X_umap']
  layers         counts
  normalization  none in the file; recorded on dataset MYB41 transgene (id 14)
  obs columns    barcode, sample, n_genes_by_counts, total_counts, total_counts_organellar,
                 pct_counts_organellar, is_cell, dev_label, saturn_Celltype, saturn_Celltype_knn,
                 saturn_Celltype_conf, saturn_Celltype_agree, saturn_celltype_fine,
                 saturn_celltype_fine_knn, saturn_celltype_fine_conf, saturn_celltype_fine_agree,
                 saturn_time_celltype, saturn_time_celltype_knn, saturn_time_celltype_conf,
                 saturn_time_celltype_agree, saturn_timezone, saturn_timezone_knn,
                 saturn_timezone_conf, saturn_timezone_agree, transgene_umi, transgene_pos,
                 saturn_nuc_Celltype, saturn_nuc_Celltype_knn, saturn_nuc_Celltype_conf,
                 saturn_nuc_Celltype_agree, singler, singler_score, sr_Atrichoblast (elongation),
                 sr_Atrichoblast (mature), sr_Cortex (elongation_maturation),
                 sr_Cortex (maturation), sr_Cortex maturation, sr_Cortex_Atrichoblast (maturation),
                 sr_Endodermis (elongation_maturation), sr_LRC, sr_Meristem, sr_Pericycle,
                 sr_Pericycle_endodermis (elongation), sr_Periderm_endodermis, sr_Phellem,
                 sr_Phellogen, sr_Phloem, sr_QC, sr_Trichoblast (elongation_maturation),
                 sr_Trichoblast (mature), sr_Unknown_1, sr_Xylem, nn_label, nn_label_plain,
                 nn_source, nn_conf, nn_pct_shahan
  dataset        MYB41 transgene (id 14) — already loaded from this file
  cell types     23: Atrichoblast, Atrichoblast (elongation), Atrichoblast (mature), Columella,
                 Cortex, Cortex (elongation/maturation), Cortex (maturation), Cortex maturation,
                 Cortex/Atrichoblast (maturation), Endodermis (elongation/maturation), LRC,
                 Lateral Root Cap, Meristem, Pericycle, Pericycle/endodermis (elongation), Phellem,
                 Phloem, Procambium, QC, Trichoblast, Trichoblast (elongation/maturation),
                 Unknown_1, Xylem
  samples        Col-0 2,442, pFACT 3,304, pHORST 2,937
  units          log1p normalised counts
Upload this file? Its dataset is already loaded [y/N]:
```

For a new dataset the dataset line reads `— new, registered by this upload`, a `gene counts`
line says how many genes' counts will be written and where (`27,656 genes, one object each
under scrna/counts/My_dataset_<new id>_/`), and the question is
`Upload this file and load its cells into '<name>'?`.

The summary and the question are written to the terminal (stderr), so they still appear
when the output is redirected to a file. Every name read from the file is shown with its
control characters escaped (`\x1b`, `\n`), so the file cannot alter the summary being
confirmed. Pressing Enter answers no.

`--yes` goes ahead without asking; without a terminal to ask in, the command refuses unless
`--yes` is given. `--dry-run` signs in and runs every check, shows the summary, and stops
before sending or writing anything.

Once confirmed, it sends the gzipped file through storage's resumable upload. Because an object is
named by the fingerprint of its contents, storage already holding that name means it holds
this very file, byte for byte: the command says so and sends nothing. If the connection
drops, run the same command again: the gzipped copy and what identifies the upload wait in
`~/.bloom/scrna-uploads/`, and the transfer continues from the last byte storage received.
That is also where "what is prepared is kept" refers to, in the messages below — delete the
files there to start an upload over from the beginning. An upload recorded for another
server, or for a gzipped copy that has since been rewritten, is started afresh rather than
resumed.

Where storage takes every byte and still stores nothing, the command says so and forgets the
server's upload while keeping the gzipped copy: the protocol will not finish an upload that
is already at full length, so the next run sends a fresh one rather than repeating the same
failure. A login lasts about an hour and a large file can take longer, so a session expiring
part-way through is ordinary rather than exceptional: the credentials that made it are the
ones on disk, so the command signs in again itself and carries on from the last byte
storage took. It does that once — a second expiry is not a token running out, and is
reported. A login that is refused outright is reported at once instead, since signing in
again does nothing about a permission the account does not have.

Once the file is stored, the cells are written: the dataset row (recording the file's
fingerprint, the load's options and how many cells the file holds), its cell-type catalogue
(each type with its colour), and one row per cell with its UMAP position, type and sample.
Then each gene's counts: a row per gene, numbered by its position in the file, and one object
per gene under `counts/<name>_<dataset id>_/<gene>.json` holding its value in every cell that
has one, keyed by the cell's position. The matrix is read a block of genes at a time, so the
memory this takes stays bounded whatever the file's size.

The dataset is marked finished last, after every cell is read back and every gene's counts
are recorded, with the file's `uns['normalization']`; until then a dataset that records a
file but no finish time is an unfinished load. A dataset finished before the counts were
part of the upload gets the ones it is missing when the same command is run again; it is
marked unfinished while they are written, and finished again once they are all there. The
colour-bar units follow the normalization (`log1p normalised counts`,
`log2(x+1) normalised counts`, …) unless `--expression-units` says otherwise.

On a terminal, the slow steps — preparing (fingerprinting and gzipping) the file, uploading
it, writing the cells and writing the genes — each show a progress bar with how much is
done, e.g. `Uploading my_dataset.h5ad ━━━━━━━━╺━━━━━━ 75.0/182.4 MB 41% 0:00:38`; a log or CI run gets
only the result lines.

Each write is one request sent once; if one fails the command says the file is stored and
the load stopped, and running the same command again continues it. A write whose outcome
is unknown (a timeout, or Ctrl-C or a closed terminal while it was being sent) may still be finishing on the
server, so the next run waits that out first, about six minutes, saying how many seconds
are left. Load a dataset from one terminal at a time.

`--name`, `--species` and `--annotation` (the column of cell-type or cluster labels) are all
a load needs; `--create` is added the first time, to register the dataset. A dataset name
holds letters, digits, spaces, `.`, `_` and `-`. Anything else the file records per cell can
come along too. Each of these options names an `obs` column:

- `--genotype-column COLUMN` makes each of its values a genotype the cells point at.
  `--control GENOTYPE` names the control (it is never guessed), and `--construct
  GENOTYPE=NAME` (repeatable) the construct a line carries.
- `--facet COLUMN` (repeatable) turns the column's values into filters on the map: a
  treatment, a timepoint, a batch, whether a transgene was detected. A column may have at
  most 12 values of at most 200 characters, a column name at most 64, and a load at most 32
  such columns; a column with more values, such as a count or a score, is refused as a
  measurement rather than a label.
- `--source-column COLUMN` records, per cell type, where its label came from, such as the
  reference atlas it was transferred from.

```bash
bloomctl scrna hdf5 upload my_dataset.h5ad --name "My dataset" \
  --species Arabidopsis --annotation cell_type --create \
  --genotype-column genotype --control Col-0 --construct line1=35S:GENE \
  --facet treatment --facet timepoint --source-column label_source -p staging
```

On a dataset already loaded from this file, `--add-labels` with any of these adds them to its
cells, which stay as they are. Labels it already has are kept: a new `--facet` joins them,
and one of a column it already has replaces that column's values.

`--run-id ID` is for the RNA-seq pipeline, which loads each Cell Ranger run's result signed in
as `bloom_workflows`. It records the run on the dataset it creates, so the run can be linked to
it, and refuses to continue a dataset another run, or a person, started. An upload without it
works as before.

**List** needs any login. It reports what the bucket holds — each object's fingerprint,
its size in bytes, when it arrived, and the dataset recording that fingerprint, where one
does; an object can be stored before any dataset points at it, so an unnamed row is
expected rather than a fault. A search keeps the entries whose fingerprint or dataset name
contains it, and `--file` puts the question the other way round: it fingerprints a local
file and reports whether storage already holds it, which is how to tell an interrupted
upload from a finished one. The table shortens each fingerprint; `--output json` carries
all 64 characters.

**Download** needs any login. It streams the object, decompresses it and checks
its SHA-256 as it goes, and moves the file into place only when the fingerprint
matches; a mismatch leaves nothing behind. A file already at the destination with
the right fingerprint is left alone, and a different one is never overwritten.


## `bloomctl genome upload`

Reference genomes for the Cell Ranger workflow are kept in Bloom with numbered
versions. Each version is one gzipped FASTA and one gzipped GTF in the
`genome-references` bucket, at `<genome>/v<N>/genome.fa.gz` and
`<genome>/v<N>/genes.gtf.gz`, with their SHA-256 and size recorded. A version's
files never change once uploaded; a different FASTA or GTF is a new version.

```bash
# A new genome: --species (the common name) is required.
bloomctl genome upload tair10_araport11 \
  --fasta TAIR10.fa --gtf Araport11.gtf --species Arabidopsis \
  --assembly TAIR10 --annotation Araport11 \
  --source-url https://www.arabidopsis.org/ -p staging
# → tair10_araport11 v1: ready

# The next version of the same genome.
bloomctl genome upload tair10_araport11 --fasta TAIR10.fa.gz --gtf Araport11_2024.gtf.gz -p staging
# → tair10_araport11 v2: ready
```

- The genome name is 1 to 64 lowercase letters, digits, `.`, `_` or `-`, starting with a
  letter or digit, with no `__` and not ending in `.v` and a number (that is how a version is
  written). It can never be changed.
- The files can be plain or gzipped. A plain file is gzipped into a temporary
  folder first; the original is never changed. A gzipped file is read to the end
  to check it is intact, then sent as it is.
- Before anything is sent, the FASTA must start with a `>` line, the GTF's first
  record must have 9 tab-separated columns, and each gzipped file must be at most
  500 MB.
- The upload is described on the terminal and goes ahead once confirmed; `--yes`
  skips the question.
- If the upload stops part-way, the version is marked abandoned and is never
  offered for a run. Run the same command again to upload it as a new version.
- Needs a writer login (`bloom_writer`).

## `bloomctl genome list` and `download`

```bash
bloomctl genome list -p staging                  # every genome, version and status
bloomctl genome download tair10_araport11 --to ref/ -p staging       # newest ready version
bloomctl genome download tair10_araport11.v1 --to ref/ -p staging    # a named version
# Which version a name means, without downloading (prints e.g. tair10_araport11.v2):
bloomctl genome download tair10_araport11 --version-only
# For building a Cell Ranger reference (what the workflow runs):
bloomctl genome download tair10_araport11 --to ref/ --unzip --version-file ref/VERSION
```

- A version is **ready** once its upload finished. `uploading`, `abandoned` (the upload
  failed) and `withdrawn` (pulled by an admin, with a reason) versions are listed but never
  downloaded; asking for one says which it is.
- `download` writes `genome.fa.gz` and `genes.gtf.gz`, or `genome.fa` and `genes.gtf` with
  `--unzip`. Each file is streamed to a hidden name and checked against the SHA-256 and size
  Bloom recorded at upload before it is moved into place; `--unzip` unzips only a checked file.
  A file that doesn't match is not saved; files already checked stay in place.
- Run it again and a file already in place with the right content is kept, so only what is
  missing is fetched; a different file there is never overwritten.
- The only line on stdout is the exact version, as `tair10_araport11.v2` (the form the command
  takes); `--version-file` also writes it to a file. `--version-only` resolves and prints it
  without downloading, so a workflow can check for a reference it already built.
- Exit codes: `0` done; `3` the version isn't available (unknown, not ready) or a file doesn't
  match Bloom's record, which running again won't fix; `1` anything else, such as a network or
  storage error, worth retrying; `2` a usage error.
- `list --output json` (or `csv`) gives one record per version.
- Any login can run both, including the pipeline's (`bloom_workflows`).

## Access & roles

Commands run **as the logged-in user** — every query and mutation is RLS-enforced
under the caller's role, not a service key. So the role your `bloomctl login`
profile maps to determines what works:

| Command tag                                                                                    | Required role                         | Intended user                                                                             |
| ---------------------------------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------- |
| **[read]** (`download`, `download-for-predict`, `batch-download-for-predict`, `datasets list`, `scrna hdf5 download`, `scrna hdf5 list`, `genome list`, `genome download`) | `bloom_user` (any authenticated user) | anyone with a Bloom account                                                               |
| **[write]** (`ingest-result`, `batch-ingest-result`, `datasets create`, `scrna hdf5 upload`, `genome upload`)                        | `bloom_writer` / `bloom_admin` (`genome upload`: `bloom_writer` only)        | automated pipelines (e.g. the trait-extraction write-back), or users granted write access |

A read-only `bloom_user` can `list` datasets but **cannot** `create` one — the
write path (the `create_cyl_dataset` / `insert_cyl_result_envelope` RPCs and the
underlying table inserts) is granted to `bloom_writer`/`bloom_admin`. Point the
**[write]** commands at a profile with write access (e.g. the pipeline's service
account); a `bloom_user` login will get a clear permission error.

## `bloomctl cyl ingest-result`

Ingest one per-scan `ResultEnvelope` (emitted by the sleap-roots trait extractor)
into Bloom by calling the `insert_cyl_result_envelope` RPC.

```
bloomctl cyl ingest-result <envelope.json | ->   [-p/--profile PROFILE] [--json] [--predictions-dir DIR]
```

- Reads the envelope from a file path, or from **stdin** when the argument is `-`.
- **Validates** it against `sleap-roots-contracts` before the call (fails fast with
  a readable message) and sends the original JSON unchanged.
- **Idempotent:** re-ingesting the same envelope is a no-op (first-writer-wins on
  the envelope's `idempotency_key`), reported as "already ingested" — not an error,
  with one exception: see `ARGO_WORKFLOW_NAME` below.
- `--json` prints the RPC's result object (including `source_id`) to stdout for
  scripting; without it, a human-readable summary line.
- `--predictions-dir DIR`: construct and upload the envelope's `blobs`. Reads
  `DIR/{scan_key}.predictions.json` (a `PredictionManifest`, from
  `sleap-roots-contracts` v0.1.0a5+), verifies each artifact's `.slp` bytes
  against its declared checksum, uploads them to the `cyl-intermediates`
  storage bucket, and merges the resulting `BlobRef`s into the envelope before
  ingesting. Idempotent per-blob (skips re-upload if an identical object
  already exists at the derived path) and fails fast — before any upload or
  RPC call — on a missing/malformed manifest, a missing `.slp` file, a
  checksum mismatch, or a blob already present in the envelope. Omit to
  forward `blobs` unchanged, exactly as before this flag existed.

  If the envelope's `idempotency_key` is already in `cyl_trait_sources`, the
  upload is skipped and the constructed blobs are not merged: the RPC discards
  them anyway, and re-uploading is the one step that can fail once the producer
  has recomputed its artifacts, since `.slp` output is not byte-reproducible
  and the object path embeds the key (talmolab/sleap-roots-pipeline#76).
  Checksum verification is part of the upload, so it is skipped on that path
  too — the local bytes are never stored, so their integrity is not something
  the delivery can affect. Every other guarantee above still applies to a
  re-delivery, because the check runs after the manifest is read.
- When the `ARGO_WORKFLOW_NAME` environment variable is set (Argo sets it
  automatically inside the write-back container — see
  `sleap-roots-write-back-template.yaml`), also links the matching
  `cyl_pipeline_run_scans` row to this write-back (`'written'`), so the
  pipeline run's `done_count`/`failed_count` can reflect it. The value is sent
  whitespace-stripped (`pipeline_run_id_from_env()`, the same run id
  `batch-ingest-result` scopes and reconciles with). Omit, unset or blank it for
  the existing manual/ad-hoc invocation shape, which is unaffected.

  If the RPC then reports that it updated no row of this workflow
  (`status_update_matched: false`), the command prints the outcome and exits
  non-zero. For a written delivery the message says the data was written but
  the row was not updated. For an already-ingested envelope (a no-op) it says
  nothing was written and this workflow's row for the source's scan was not
  updated: the source's scan could not be resolved (no recorded scan and no
  run row carrying the source), this workflow did not dispatch that scan, the
  row is already `'failed'`, or the row is already linked to a different
  source. A no-op whose row was updated, under this workflow name
  or a new one, still exits zero.

The most common real-world error is `inputs.image_ids` not resolving to exactly
one scan on the target server — the command explains that the scan's images must
already exist in `cyl_images` on the Bloom you're pointed at.

Auth: uses your saved login profile, which must be allowed to run the write-back
RPC (`bloom_writer` / `bloom_admin`, or `bloom_workflows` for the cluster
pipeline). The pipeline's write-back pods don't run `bloomctl login`: they read
`credentials.txt` from a mounted Secret. See `services/workflows/README.md`
"Provisioning (per environment)" step 6. Other non-interactive use is #398.

Examples:

```
bloomctl cyl ingest-result path/to/scan.result.json
cat scan.result.json | bloomctl cyl ingest-result - --json
```

## `bloomctl cyl batch-ingest-result`

Write back a batch of per-scan `ResultEnvelope`s in one invocation — the batch
sibling of `ingest-result`, for the A4 per-batch pipeline's `write-back` Argo
task.

```
bloomctl cyl batch-ingest-result <envelopes_dir>
  [-p/--profile PROFILE] [--json] [--predictions-dir DIR]
```

- Ingests every `{scan_key}.result.json` file directly under `envelopes_dir`
  (non-recursive — the flat layout `trait_extractor.extract_batch`'s
  output produces), via the same validation + RPC path as `ingest-result`.
  Discovery is scoped to the run's manifest, resolved with
  `sleap_roots_contracts.load_run_manifest` (bloom #934):
  - **With `ARGO_WORKFLOW_NAME` set** (whitespace stripped — the same run id
    is sent as `p_argo_workflow_name` and used for the reconciliation below):
    `run_manifest.<ARGO_WORKFLOW_NAME>.json`, else a legacy `run_manifest.json`
    **only if it names this run** (accepted during the rollout;
    sleap-roots-pipeline#82 removes the fallback). If there is **no manifest
    for this run** — neither file, or only a legacy file naming a different
    run (stale, or another run's) — nothing is ingested: the batch reports a
    failed `scan_key="<run-manifest>"` entry naming the files (and both run
    ids), still makes the reconciliation call below, recording that no run
    manifest reached write-back, and exits `1`. It never falls back to
    ingesting every envelope in a directory that other runs share, or to
    another run's scope. A per-run file naming a different run, or an
    `ARGO_WORKFLOW_NAME` the contract rejects, fails before anything is
    ingested.
  - **Without it** (unset or blank): `run_manifest.json` scopes discovery when
    present; with no manifest, discovery is fully unscoped, as above.

  Don't recover a failed pipeline batch by running `batch-ingest-result` by
  hand over the pipeline's shared `a4_poc` directories: with
  `ARGO_WORKFLOW_NAME` set it fails the same way, and without it, once the
  stale legacy manifests are gone, discovery is unscoped and ingests every
  run's envelopes. Re-dispatch the run instead.

  When a manifest scopes discovery, out-of-scope files are excluded (and
  logged at debug level), and a declared `scan_key` with no matching file is
  reported as a batch failure — unless a differently-named file's own content
  actually reports that scan_key (a filename/body mismatch), in which case the
  real outcome wins and the failure is dropped.
- **Isolates per-envelope failures** — an unreadable/malformed file, a
  contract-validation failure, or a mapped RPC error is recorded and reported,
  but does not abort the rest of the batch.
- **No-op re-deliveries are reported `skipped`**, not `failed` — same
  first-writer-wins idempotency as `ingest-result`. The exception is the same
  as `ingest-result`'s: with `ARGO_WORKFLOW_NAME` set, a no-op whose row was
  not updated is reported `failed`, non-retriable, with the same message.
- `--predictions-dir DIR`: predict's own nested batch output root
  (`DIR/{scan_key}/{scan_key}.predictions.json` + `.slp` files per scan).
  Constructs, verifies, and uploads blobs per envelope from its own scan_key's
  subdirectory, reusing `ingest-result --predictions-dir`'s logic unchanged — so
  an envelope whose `idempotency_key` is already in `cyl_trait_sources` has its
  upload and merge skipped, exactly as for the single-envelope command. Blob
  construction still runs either way, so a missing manifest or a missing `.slp`
  fails that envelope whether or not it was already ingested. A missing manifest
  or upload failure isolates that envelope without aborting the others.
- `--json` prints one entry per envelope (`scan_key`, `status`, `error`,
  `retriable`, `warning`) as a JSON array; without it, a human-readable summary
  plus one line per failure and one `WARNING` line per degraded item. `warning`
  is non-empty when the idempotency-gate check could not run and the command
  fell back to uploading — most likely a missing column grant.
- **Exit code:** non-zero if any failed entry in the batch is retriable (an
  envelope, a missing `scan_key`, a missing run manifest, or the
  reconciliation call); zero if every envelope succeeded, was a no-op
  re-delivery, or failed only non-retriably, or if there was nothing to ingest
  and no manifest was needed (`ARGO_WORKFLOW_NAME` unset, empty directory, no
  `run_manifest.json`). A directory containing only a manifest with no
  matching files is not the empty case — it exits non-zero. A missing or
  unreadable `envelopes_dir`, or a manifest that can't be read, or a per-run
  manifest naming the wrong run, exits `1` before anything is ingested.
- When `ARGO_WORKFLOW_NAME` is set (and not blank), after every discovered envelope has been
  processed, marks every scan dispatched under that workflow name that never
  produced a result as `'failed'` (one call, regardless of batch size —
  including a batch of zero envelopes, since every scan under that workflow
  name having failed prediction before producing any file is exactly the
  case this closes out). Skipped entirely when the env var is unset or blank (manual/
  local runs, unaffected). A failure of this call is isolated, not a crash —
  it's reported as its own failed entry (`scan_key="<reconciliation>"`) in the
  batch's summary/`--json` output and reflected in the exit code, alongside
  every real envelope's own outcome; a successful call logs how many scans it
  closed out. **Exception (bloom #1034):** when an envelope the batch tried to
  ingest failed retriably, the call is skipped and stderr says
  `reconciliation deferred to the status poller: N envelope(s) failed
  retriably`. Argo retries the write-back step in the same Workflow, and
  closing those scans now would keep them `'failed'` even after the retry
  ingests them. The status poller closes whatever is still `'queued'` once
  the Workflow ends. A missing declared file, a missing run manifest and a
  non-retriable failure don't count, since a retry can't change them. An
  unreadable envelope file (e.g. a truncated file left by an OOM-killed
  producer) is isolated without aborting the batch, and, being retriable,
  defers the call too.

Auth: same saved login profile as `ingest-result` (must have write access).

Example:

```
bloomctl cyl batch-ingest-result ./results --predictions-dir ./predict-out
```

## Dev-stack smoke test

`tests/test_dev_stack_smoke.py` verifies the local Supabase stack is serving and
that `bloomctl cyl ingest-result` can round-trip against it (gateway `/rest`+`/auth`
= 200, the write-back RPC is migrated, and a seed → ingest → no-op → cleanup cycle
succeeds). It self-skips unless `BLOOMCTL_DEV_SMOKE` is set, and is marked
`integration` so the default suite and CI never run it. After `make dev-up`:

```
set -a; . ./.env.dev; set +a
BLOOMCTL_DEV_SMOKE=1 uv run --extra test --with psycopg \
  --project bloomcli pytest bloomcli/tests/test_dev_stack_smoke.py -v
```
