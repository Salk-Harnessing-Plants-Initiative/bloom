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

`login` is flat; assay-specific commands are grouped by data type (`cyl`). Each
command is tagged **[read]** or **[write]** — see [Access & roles](#access--roles).

- `bloomctl login` — bootstrap client config from the Bloom server and store
  credentials per profile.
- **[read]** `bloomctl cyl download <out_dir> …` — download a cylinder experiment
  or single scan (metadata `scans.csv` + per-frame images). Select the experiment
  by `--experiment-id N`, `--scan-id N`, or `--experiment-name "<text>"` (a
  case-insensitive substring search on the experiment name, run server-side and
  index-backed; `--species` to narrow; an ambiguous name lists the candidates and
  exits without downloading).
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
- **[read]** `bloomctl cyl datasets list` — list cylinder trait datasets (all by
  default). Scope to one experiment with `--experiment-id N` (scriptable) or
  `--experiment` to **pick one from a menu** (needs a terminal). `--output csv|json`
  for machine-readable output.
- **[read]** `bloomctl cyl datasets get <name>` — show one dataset's details and the
  unique traits it contains, via the `cyl_dataset_trait_names` view (`--json` output).
- **[write]** `bloomctl cyl datasets create <name> <experiment_id> <trait_source_name>` —
  create a trait dataset (`--qc-set-name` to exclude a QC set, `--timepoints`).
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

Run `bloomctl <command> --help` for the full option list of any command.

## Common conventions

These apply across the `cyl` commands, so the per-command sections below stay short:

- **Profiles** — every command takes `-p/--profile <name>` (default `prod`). `bloomctl login`
  writes a profile; use separate profiles to keep prod / staging / local logins side by side
  (`bloomctl login --server <url> -p staging`, then `… -p staging` on any command).
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
  [--limit N] [--overwrite] [-n/--workers N] [-p/--profile PROFILE]
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
`--plant-age-min/--plant-age-max` (age window in days, default 0–1000), `--limit` (max scans,
default 100000).

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

### Resuming an interrupted download

A download is **resumable**: frames already in `<out_dir>` are kept and counted as
`already present`, so re-running the same command fetches only what is still missing. That
applies to any interruption — a failed run, `Ctrl-C`, a dropped network, a closed laptop:

```bash
bloomctl cyl download ./out --experiment-id 42   # interrupted at 117k of 414k frames
bloomctl cyl download ./out --experiment-id 42   # picks up from 117k
```

Pass `--overwrite` to ignore what's on disk and re-fetch every frame.

> **If the directory was written by bloomctl 0.1.0a3 or earlier**, re-fetch it once with
> `--overwrite`. Those releases wrote frames non-atomically, so an interrupted run could leave a
> truncated file behind, and resume treats any non-empty file as complete.

Long runs keep working past the point where the login's token is refreshed, so a download that
takes several hours completes. If a storage error does mention an expired session, re-running the
command resumes from wherever it stopped.

> `--experiment-name` requires the `cyl_experiment_search` DB function (migration
> `…_add_cyl_experiment_search.sql`) to be applied on the server you're pointed at.

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
bloomctl cyl download-for-predict <scan-id> <out>   [-p/--profile PROFILE]
```

- Writes frames to `<out>/scan_<scan_id>/<frame_number><ext>`.
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
  [-p/--profile PROFILE] [--json]
```

- Exactly one of `--scan-ids-file` (a JSON array of integer scan_ids, read from
  a path or stdin when the value is `-`) or `--scan-ids` (a comma-separated
  list, for ad hoc manual use) is required.
- Stages every scan_id into `<out_dir>/scan_<scan_id>/`, identical to what
  `download-for-predict` writes for one scan.
- **Isolates per-scan failures** — one bad scan (not found, no frames, a
  metadata-resolution failure, a partial frame-download failure) is recorded
  and reported, but does not abort the rest of the batch.
- **Skips an already-staged scan** — if `<out_dir>/scan_<scan_id>/` already has
  a valid sidecar (parses, `scan_key` matches), that scan is reported
  `skipped` and not re-downloaded.
- `--json` prints one entry per scan_id (`scan_key`, `status`, `error`) as a
  JSON array; without it, a human-readable summary plus one line per failure.
- **Exit code:** non-zero if any scan in the batch failed; zero if every scan
  succeeded, was skipped, or the input was empty.

Auth: same saved login profile as other `cyl` commands.

Example:

```
bloomctl cyl batch-download-for-predict ./staged --scan-ids-file scan_ids.json
```

## Access & roles

Commands run **as the logged-in user** — every query and mutation is RLS-enforced
under the caller's role, not a service key. So the role your `bloomctl login`
profile maps to determines what works:

| Command tag | Required role | Intended user |
|---|---|---|
| **[read]** (`download`, `download-for-predict`, `batch-download-for-predict`, `datasets list`) | `bloom_user` (any authenticated user) | anyone with a Bloom account |
| **[write]** (`ingest-result`, `batch-ingest-result`, `datasets create`) | `bloom_writer` / `bloom_admin` | automated pipelines (e.g. the trait-extraction write-back), or users granted write access |

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
  the envelope's `idempotency_key`), reported as "already ingested" — not an error.
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

The most common real-world error is `inputs.image_ids` not resolving to exactly
one scan on the target server — the command explains that the scan's images must
already exist in `cyl_images` on the Bloom you're pointed at.

Auth: uses your saved login profile, which must have write access
(`bloom_writer` / `bloom_admin`). Non-interactive / scoped credentials for
cluster/CI use are tracked separately (#398).

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
- **Isolates per-envelope failures** — an unreadable/malformed file, a
  contract-validation failure, or a mapped RPC error is recorded and reported,
  but does not abort the rest of the batch.
- **No-op re-deliveries are reported `skipped`**, not `failed` — same
  first-writer-wins idempotency as `ingest-result`.
- `--predictions-dir DIR`: predict's own nested batch output root
  (`DIR/{scan_key}/{scan_key}.predictions.json` + `.slp` files per scan).
  Constructs, verifies, and uploads blobs per envelope from its own scan_key's
  subdirectory, reusing `ingest-result --predictions-dir`'s logic unchanged. A
  missing manifest or upload failure isolates that envelope without aborting
  the others.
- `--json` prints one entry per envelope (`scan_key`, `status`, `error`) as a
  JSON array; without it, a human-readable summary plus one line per failure.
- **Exit code:** non-zero if any envelope in the batch failed; zero if every
  envelope succeeded, was a no-op re-delivery, or the directory was empty.

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
