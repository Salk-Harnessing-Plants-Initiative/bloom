# bloomctl

Command-line tool for the **Bloom Database** (Salk Harnessing Plants Initiative) — log in, find cylinder experiments, download their images and metadata, and work with cylinder  trait datasets.

## Install

Releases are still pre-releases (`0.1.0aN`), so install by **asking for the version by name**:

```bash
uv tool install "bloomctl==0.1.0a4"    # isolated CLI tool (recommended)
uvx bloomctl@0.1.0a4 --help            # one-off, no install
pip install "bloomctl==0.1.0a4"        # into the active environment
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

## Commands

**Find & download** (any logged-in user):

| Command                          | What it does                                                                                                               |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `cyl experiments list`         | List experiments (species · name · id); filter with `--species NAME` or `--species-menu`                                  |
| `cyl accessions list`          | Accessions in an experiment (`--experiment-id`, or pick from a menu)                                                     |
| `cyl accessions sample-counts` | Plant count per accession/species (`--species NAME`, or `--species-menu`)                                                 |
| `cyl datasets list` / `get`  | List trait datasets (`--experiment` menu) / show one dataset's traits                                                    |
| `cyl qc list-sets`             | List cylinder QC sets                                                                                                      |
| `cyl download <dir>`           | Download an experiment/scan:`scans.csv` + images. Select by `--experiment-id`, `--scan-id`, or `--experiment-name` |

**Pipeline** (stage-in / write-back):

| Command                                                       | What it does                                                        |
| ------------------------------------------------------------- | ------------------------------------------------------------------- |
| `cyl download-for-predict` / `batch-download-for-predict` | Stage scan(s) into the predict-ready layout                         |
| `cyl ingest-result` / `batch-ingest-result`               | Write per-scan pipeline results back to Bloom*(needs write access)* |
| `cyl datasets create`                                       | Create a trait dataset*(needs write access)*                        |

Run `bloomctl <command> --help` for the full options of any command.

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
