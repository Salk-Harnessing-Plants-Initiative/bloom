# bloomctl

Command-line tool for the **Bloom Database** (Salk Harnessing Plants Initiative): log in and access data from Bloom, spanning cylinder experiments, plate scanners and expression data.

| I want to…                                   | Go to                          |
| -------------------------------------------- | ------------------------------ |
| Install bloomctl                             | **1. Install**                 |
| Sign in                                      | **2. Log in**                  |
| Download cylinder scans, traits or datasets  | **3. Cylinder experiments**    |
| Download plate (GraviScan) images            | **4. Plate experiments**       |
| Upload or download single-cell `.h5ad` files | **5. Single-cell data**        |
| Run bloomctl inside a pipeline               | **6. Pipelines and containers** |

---

## 1. Install

Releases are still pre-releases (`0.1.0aN`), so install by **asking for the version by name**:

```bash
uv tool install "bloomctl==0.1.0a5"    # isolated CLI tool (recommended)
uvx bloomctl@0.1.0a5 --help            # one-off, no install
pip install "bloomctl==0.1.0a5"        # into the active environment
```

Check it worked:

```bash
bloomctl --version
```

> **Don't add `--pre` or `--prerelease=allow`.** Those flags aren't specific to `bloomctl` —
> they let *every* dependency install an unfinished dev version too.

---

## 2. Log in

Do this once. It asks for your Bloom email and password and saves them to `~/.bloom`.

```bash
bloomctl login
```

- Every command logs in to `prod` by default. Use `-p/--profile` to pick a different login.
- Browsing and downloading work for any account. Commands marked *(needs write access)* need an
  account that has been given write access.

---

## 3. Cylinder experiments

### Quickstart

```bash
# Find an experiment: pick a species from a menu, note its id
bloomctl cyl experiments list --species-menu

# Download it, by id or just by name
bloomctl cyl download ./out --experiment-id 42
bloomctl cyl download ./out --experiment-name "drought 2024"
```

That writes `./out/scans.csv` (one row per scan) and the per-frame images.

### Before a big download

Check what's in the experiment first:

```bash
bloomctl cyl accessions list --experiment-id 42          # which accessions are in it
bloomctl cyl accessions sample-counts --species-menu     # plant count per accession
bloomctl cyl datasets list --experiment-id 42            # trait datasets already built
bloomctl cyl download ./out --experiment-id 42 --meta-only   # scans.csv only, no images
```

### Tips

- **Speed.** Downloads fetch 8 frames at a time. On a fast connection, raise it with
  `--workers 16` (up to 64). If frames start failing because the server is refusing requests,
  use a *lower* number, not a higher one.
- **If a download stops part-way, run the same command again.** It keeps what is already on disk
  and fetches only what is missing. Use one output directory per experiment.

### The download log

Every `cyl download` writes `download_log.txt` next to `scans.csv`: one line per frame and a
summary at the bottom. It is the file to send us if something looks wrong.

```
OK   scan=1 frame=0 cyl-images/0.png
SKIP scan=1 frame=1 cyl-images/1.png
FAIL scan=1 frame=3 cyl-images/3.png  error=[Errno 28] No space left on device
UNLISTED scan=5 (frame count unknown)  error=...
NOFRAMES scan=7 (no images recorded for this scan)

Summary: 3/8 frames present (3 downloaded this run, 0 already on disk), 5 failed
```

| Status     | What it means                                                                  |
| ---------- | ------------------------------------------------------------------------------ |
| `OK`       | Downloaded during this run                                                     |
| `SKIP`     | Already on disk, so not fetched again. A log full of these is a resumed run.   |
| `FAIL`     | This frame is missing; `error=` says why                                       |
| `UNLISTED` | The scan's frame list couldn't be fetched, so an **unknown** number is missing |
| `NOFRAMES` | The scan has no images in Bloom. Nothing to download; not a failure.           |

**Watch for `UNLISTED`.** A `FAIL` is one missing frame, but an `UNLISTED` scan means we don't
know how many are missing, so a run can report every frame present and still be incomplete.
Re-running picks up both. If the run stopped early (for example, the disk filled up), the
summary line ends with the reason.

### Cylinder commands

| Command                          | What it does                                                                          |
| -------------------------------- | ------------------------------------------------------------------------------------- |
| `cyl experiments list`           | List experiments (species · name · id); filter with `--species NAME` or `--species-menu` |
| `cyl accessions list`            | Accessions in an experiment (`--experiment-id`, or pick from a menu)                  |
| `cyl accessions sample-counts`   | Plant count per accession/species (`--species NAME`, or `--species-menu`)             |
| `cyl datasets list` / `get`      | List trait datasets (`--experiment` menu) / show one dataset's traits                 |
| `cyl datasets create`            | Create a trait dataset *(needs write access)*                                         |
| `cyl qc list-sets`               | List cylinder QC sets                                                                 |
| `cyl download <dir>`             | Download `scans.csv` + images. Select by `--experiment-id`, `--scan-id` or `--experiment-name` |

---

## 4. Plate experiments

Plate (GraviScan) experiments work the same way, with `plate` in place of `cyl`.

### Quickstart

```bash
# Download a whole plate experiment, by id or just by name
bloomctl plate download ./gravi --experiment-id 12
bloomctl plate download ./gravi --experiment-name "gravitropism" --species Arabidopsis
```

That writes `./gravi/plates.csv` and `plate_sections.csv` (metadata) and the plate images.

### Download only part of an experiment

```bash
bloomctl plate download ./gravi --experiment-id 12 --plate-id PLATE-001   # one plate
bloomctl plate download ./gravi --experiment-id 12 --wave-number 3        # one wave
bloomctl plate download ./gravi --experiment-id 12 --meta-only            # csv only, no images
```

### Tips

- **Start with `--meta-only`.** A plate session captures one image per plate per cycle, so a
  multi-day experiment is thousands of files. The metadata shows what you're about to pull.
- **`--limit N`** fetches at most N scans, to look at a sample. It isn't a way to export an
  experiment in parts, so give a sample and a full download separate directories.
- **`--workers`** raises the download speed, as for cylinders.
- **Resuming** works as for cylinders, and a little better: plate images record their size, so a
  file cut short by a dropped connection is fetched again instead of being treated as complete.

### Plate commands

| Command                | What it does                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `plate download <dir>` | Download `plates.csv` + `plate_sections.csv` + images. Same selectors as `cyl download`, narrowed with `--plate-id` or `--wave-number` |

---

## 5. Single-cell data

A single-cell dataset's whole AnnData file (`.h5ad`) is stored in Bloom, named by the SHA-256 of
the uncompressed file, so the same file uploaded twice is stored once.

### Quickstart

```bash
bloomctl scrna hdf5 upload my_dataset.h5ad         # needs write access
bloomctl scrna hdf5 download "My dataset"          # by name, id, or --checksum
bloomctl scrna hdf5 list                           # what storage holds
bloomctl scrna hdf5 list --file my_dataset.h5ad    # is this file already stored?
```

- `upload` checks the file's structure before sending anything. If it's interrupted, run the same
  command again and it resumes.
- `download` writes the file only once its fingerprint matches.

### Single-cell commands

| Command                    | What it does                                                                           |
| -------------------------- | -------------------------------------------------------------------------------------- |
| `scrna hdf5 upload <file>` | Store a dataset's `.h5ad`, after checking its structure *(needs write access)*          |
| `scrna hdf5 download <ds>` | Fetch a dataset's `.h5ad` by name, id or `--checksum`, checked against its fingerprint |
| `scrna hdf5 list [search]` | The dataset files storage holds; `--file` says whether a local file is stored          |

---

## 6. Pipelines and containers

These commands are for automated pipelines (stage-in and write-back), not everyday use:

| Command                                                   | What it does                                                   |
| --------------------------------------------------------- | -------------------------------------------------------------- |
| `cyl download-for-predict` / `batch-download-for-predict` | Stage scan(s) into the predict-ready layout                    |
| `cyl ingest-result` / `batch-ingest-result`               | Write per-scan pipeline results back to Bloom *(needs write access)* |

The same CLI is published as a container image:

```bash
docker run --rm ghcr.io/salk-harnessing-plants-initiative/bloomctl:staging \
  cyl ingest-result path/to/scan.result.json
```

Tags: `:staging` (latest staging build) · `:<version>` (matches the PyPI release of the same
name) · `:sha-<git-sha>` (one per commit, never changes). Build details are in the
[repo docs](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/main/bloomcli#container-image).

---

## Good to know

- **Help on any command:** `bloomctl <command> --help`.
- **Machine-readable output:** the `list` commands take `--output csv` or `--output json`.
- **Choosing a species:** `--species NAME` types it (good for scripts); `--species-menu` picks
  from a menu. Use one or the other.
- **Menus need a terminal.** In a pipe or CI job, menus stop rather than guess. For scripts, pass
  the id or name directly and use `--output json`.

## Documentation

Full docs, with per-command detail and access roles, are in the
[project repository](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/main/bloomcli).
