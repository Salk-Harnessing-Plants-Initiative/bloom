# bloomctl

Command-line tool for the **Bloom Database** (Salk Harnessing Plants Initiative) — log in, find cylinder experiments, download their images and metadata, and work with cylinder  trait datasets.

## Install

Releases are still pre-releases (`0.1.0aN`), so install by **asking for the version by name**:

```bash
uv tool install "bloomctl==0.1.0a5"    # isolated CLI tool (recommended)
uvx bloomctl@0.1.0a5 --help            # one-off, no install
pip install "bloomctl==0.1.0a5"        # into the active environment
```

```bash
bloomctl --version
```

> **Don't add `--pre` or `--prerelease=allow`.** Those flags aren't specific to `bloomctl` —
> they let *every* dependency install an unfinished dev version too.

## Quickstart for Cylinder Image Downloads

```bash
# 1. Log in once (prompts for your Bloom email + password; saves to ~/.bloom)
bloomctl login

# 2. Find an experiment — pick a species from a menu, grab its id
bloomctl cyl experiments list --species-menu

# 3. Download it — by id, or just by name
bloomctl cyl download ./out --experiment-id 42
bloomctl cyl download ./out --experiment-name "drought 2024"
```

That writes `./out/scans.csv` (metadata) and the per-frame images.

**Downloads run 8 frames at a time.** On a fast connection you can raise that:

```bash
bloomctl cyl download ./out --experiment-id 42 --workers 16    # up to 64
```

**If a download stops part-way, run the same command again.** It keeps whatever is already on
disk and fetches only what is missing, so nothing is downloaded twice.

Every command takes `-p/--profile` to target a different login (default `prod`), and the `list`
commands take `--output csv|json` for machine-readable output.

## Quickstart for Plate Image Downloads

Plate (GraviScan) experiments work the same way, with `plate` in place of `cyl`:

```bash
# 1. Log in once, if you have not already
bloomctl login

# 2. Find a plate experiment — pick a species from a menu, grab its id
bloomctl plate experiments list --species-menu

# 3. Download it — by id, or just by name
bloomctl plate download ./gravi --experiment-id 12
bloomctl plate download ./gravi --experiment-name "gravitropism" --species Arabidopsis
```

That writes `./gravi/plates.csv` and `plate_sections.csv` (metadata) and the plate images.

**Narrow it down** when you do not want the whole experiment:

```bash
bloomctl plate download ./gravi --experiment-id 12 --plate-id PLATE-001   # one plate
bloomctl plate download ./gravi --experiment-id 12 --wave-number 3        # one wave
bloomctl plate download ./gravi --experiment-id 12 --meta-only            # csv only, no images
```

**Start with `--meta-only`.** A continuous plate session captures one image per plate per
cycle, so a multi-day experiment is thousands of files — the metadata tells you what you are
about to pull. `--workers` raises the download concurrency here too, and `--limit` fetches at
most that many scans, for looking at a sample — it is not a way to export an experiment in
parts, so give a sample and a full download separate directories.

**If a download stops part-way, run the same command again.** Finished images are kept, and any
image cut off by a dropped connection is downloaded again.

## Quickstart for Finding Data

Look up what is in Bloom before you download it:

```bash
# Experiments: all, one species, or pick the species from a menu
bloomctl cyl experiments list
bloomctl cyl experiments list --species Soybean
bloomctl cyl experiments list --species-menu

# Accessions in one experiment, and plant counts per accession
bloomctl cyl accessions list --experiment-id 42
bloomctl cyl accessions sample-counts --species Soybean

# Trait datasets for an experiment, then one dataset's traits
bloomctl cyl datasets list --experiment-id 42
bloomctl cyl datasets get canola-v1

# QC sets
bloomctl cyl qc list-sets

# Plate (GraviScan) experiments, with the rig each ran on
bloomctl plate experiments list --species Arabidopsis
```

**Leave out an id and you get a menu.** `cyl accessions list` with no `--experiment-id`, or
`cyl datasets list --experiment`, lets you pick the experiment interactively.

**Every `list` command takes `--output csv|json`** (`--json` for short), for scripts and
spreadsheets:

```bash
bloomctl cyl experiments list --species Soybean --output csv > soybean_experiments.csv
bloomctl cyl experiments list --json | jq -r '.[] | "\(.experiment_id)\t\(.experiment)"'
```

**Search by name when downloading.** `--experiment-name` matches any part of the name, ignoring
case. If more than one experiment matches, it lists them and downloads nothing:

```bash
bloomctl cyl download ./out --experiment-name drought --species Soybean --meta-only
bloomctl plate download ./gravi --experiment-name gravitropism --meta-only
```

### Search and filter options

| Command | Options |
| ------- | ------- |
| `cyl experiments list` | `--species NAME` · `--species-menu` · `--limit N` (max 1000) |
| `plate experiments list` | `--species NAME` · `--species-menu` · `--limit N` (max 1000) |
| `cyl accessions list` | `--experiment-id ID` (omit for a menu) |
| `cyl accessions sample-counts` | `--species NAME` · `--species-menu` |
| `cyl datasets list` | `--experiment-id ID` · `--experiment` (menu) |
| `cyl datasets get NAME` | `--json` |
| `cyl qc list-sets` | `--include-deleted` |
| `cyl download DIR` | `--experiment-id` · `--scan-id` · `--experiment-name` · `--species` · `--plant-qr-code` · `--plant-age-min` / `--plant-age-max` (days) · `--limit` · `--meta-only` |
| `plate download DIR` | `--experiment-id` · `--scan-id` · `--experiment-name` · `--species` · `--plate-id` · `--wave-number` · `--session-id` · `--limit` · `--meta-only` |

- `--species` takes the common name (e.g. `Soybean`, `Canola`), ignoring case.
- `--species-menu` and the menus need an interactive terminal; use the plain options in scripts.
- `accessions sample-counts` totals plants across **all** experiments, not per experiment.
- `--limit` on a download fetches a sample. It is not a way to split an export, so give a sample
  its own directory.

## Quickstart for scRNA-seq Dataset Files

Upload a single-cell dataset's AnnData file (`.h5ad`) to Bloom, and download it again.
Uploading checks the file first, which needs `h5py` and `numpy`:

```bash
uv tool install "bloomctl==0.1.0a5" --with h5py --with numpy
```

```bash
# 1. Upload it (needs a writer or admin login)
bloomctl scrna hdf5 upload myb41.h5ad

# 2. Check it is stored
bloomctl scrna hdf5 list --file myb41.h5ad

# 3. Download it by its fingerprint (all 64 characters, from `list --output json`)
bloomctl scrna hdf5 list --file myb41.h5ad --output json
bloomctl scrna hdf5 download --checksum <64-character fingerprint> --out copy.h5ad

# Once the dataset is loaded into Bloom, download by its exact name or its id instead
bloomctl scrna hdf5 download "MYB41 transgene"
bloomctl scrna hdf5 download 14
```

**Download by name or id only works once the dataset is loaded into Bloom.** Straight after an
upload there is no dataset yet, so use `--checksum`. A name must match exactly, including
capitals. Without `--out`, the file is saved in the current folder under the dataset's name, or
its fingerprint when you use `--checksum`.

**Upload checks the file before sending anything.** Cell and gene IDs must be unique, `X` must
hold only finite numbers, `obsm['X_umap']` must be a real two-column UMAP, and
`uns['normalization']` must say how `X` was made, among other checks. Files over 500 MB once
gzipped are refused. If a check fails, the upload stops and nothing is sent.

**If an upload stops part-way, run the same command again.** It carries on from where it
stopped. Uploading a file that is already stored sends nothing and says so.

**A new upload shows no dataset name in `list`** until the dataset is loaded into Bloom. That
is expected, not an error.

**Download checks the file as it arrives** and writes it only if it matches the stored
fingerprint. It never overwrites a different file already at `--out`.

| Command | Options |
| ------- | ------- |
| `scrna hdf5 upload FILE` | No options; needs a writer or admin login |
| `scrna hdf5 download [DATASET]` | `DATASET` is the exact dataset name or its id · `--checksum SHA256` (all 64 characters) · `--out FILE` |
| `scrna hdf5 list [SEARCH]` | `SEARCH` matches a fingerprint or dataset name · `--file FILE` · `--limit N` · `--output csv\|json` |

## Commands

**Find & download** (any logged-in user):

| Command                          | What it does                                                                                                               |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `cyl experiments list`         | List experiments (species · name · id); filter with `--species NAME` or `--species-menu`                                  |
| `plate experiments list`       | List plate experiments (species · name · rig · id); same filters                                                           |
| `cyl accessions list`          | Accessions in an experiment (`--experiment-id`, or pick from a menu)                                                     |
| `cyl accessions sample-counts` | Plant count per accession/species (`--species NAME`, or `--species-menu`)                                                 |
| `cyl datasets list` / `get`  | List trait datasets (`--experiment` menu) / show one dataset's traits                                                    |
| `cyl qc list-sets`             | List cylinder QC sets                                                                                                      |
| `cyl download <dir>`           | Download an experiment/scan:`scans.csv` + images. Select by `--experiment-id`, `--scan-id`, or `--experiment-name` |
| `plate download <dir>`         | Download a plate (GraviScan) experiment/scan:`plates.csv` + `plate_sections.csv` + images. Same selectors, narrowed with `--plate-id` or `--wave-number` |
| `scrna hdf5 upload` / `download` / `list` | Upload a single-cell dataset's `.h5ad` file *(upload needs write access)*, download it checked against its fingerprint, or list what is stored |

**Pipeline** (stage-in / write-back):

| Command                                                       | What it does                                                        |
| ------------------------------------------------------------- | ------------------------------------------------------------------- |
| `cyl download-for-predict` / `batch-download-for-predict` | Stage scan(s) into the predict-ready layout                         |
| `cyl ingest-result` / `batch-ingest-result`               | Write per-scan pipeline results back to Bloom*(needs write access)* |
| `cyl datasets create`                                       | Create a trait dataset*(needs write access)*                        |

Run `bloomctl <command> --help` for the full options of any command.

## The download log

Every `cyl download` writes `download_log.txt` next to `scans.csv`, with one line per frame and
a summary at the bottom. It is the file to send us if something looks wrong.

```
OK   scan=1 frame=0 cyl-images/0.png
SKIP scan=1 frame=1 cyl-images/1.png
FAIL scan=1 frame=3 cyl-images/3.png  error=[Errno 28] No space left on device
UNLISTED scan=5 (frame count unknown)  error=...
NOFRAMES scan=7 (no images recorded for this scan)

Summary: 3/8 frames present (3 downloaded this run, 0 already on disk), 5 failed
```

| Status     | What it means                                                                     |
| ---------- | --------------------------------------------------------------------------------- |
| `OK`       | Downloaded during this run                                                        |
| `SKIP`     | Already on disk, so it was not fetched again — this is a resumed run working       |
| `FAIL`     | This frame is missing; `error=` says why                                          |
| `UNLISTED` | The scan's frame list could not be fetched, so an **unknown** number is missing    |
| `NOFRAMES` | The scan has no images recorded in Bloom — nothing to download, not a failure      |

A log full of `SKIP` is normal: it means every frame was already on disk from an earlier run.

`UNLISTED` is the one to look out for. A `FAIL` is a single missing frame, but an `UNLISTED`
scan means we do not know how many of its frames are missing — which is why a run can report
all its frames present and still be incomplete. Re-running picks up both.

The summary line ends with the reason when the run stopped for one, such as the disk filling up.

## Run as a container

Prefer a container (e.g. a pipeline step) over a `pip install`? The same CLI is published to GHCR:

```
ghcr.io/salk-harnessing-plants-initiative/bloomctl
```

```bash
docker run --rm ghcr.io/salk-harnessing-plants-initiative/bloomctl:staging \
  cyl ingest-result path/to/scan.result.json
```

Tags: `:staging` (latest staging build) · `:<version>` (matches the PyPI release of the same name) ·
`:sha-<git-sha>` (immutable, one per commit). Image provenance and build details are in the
[repo docs](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/main/bloomcli#container-image).

## Notes

- **Species selector** — `--species NAME` filters by species (typed, scriptable); `--species-menu`
  picks from a menu. Same on every command; the two are mutually exclusive.
- **Interactive menus** (`--species-menu`, `--experiment`) need a terminal; in a pipe/CI they abort
  rather than guess. For scripting, pass the typed value/id and use `--output json`.
- **Read vs write** — browsing/downloading works for any account; the write commands
  (`ingest-result`, `datasets create`) need an account with write access.
- **`-n/--workers`** — how many frames `cyl download` fetches at once. Default `8`, maximum `64`,
  `1` for one at a time. Large experiments run tens of thousands of frames, and this is what
  makes them quick. More is not always better: if the server starts refusing requests you will
  see frames fail, and the fix is a lower number, not a higher one.
- **Resuming** — a download that stops for any reason (interrupted, connection dropped, failed
  frames) picks up where it left off when you re-run the same command in the same directory.
  Frames already on disk are skipped. One experiment per output directory.

## Documentation

Full docs — per-command detail, the container image, and access roles — are in the
[project repository](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/main/bloomcli).

## Tutorials

### Download an experiment, from login to files

A start-to-finish walkthrough for the common task — *"get me the images + metadata for the soybean
drought experiment."*

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
frames). For scripting, swap the menus for explicit ids and add `--output json`.
