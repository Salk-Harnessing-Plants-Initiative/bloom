# Changelog

All notable changes to `bloomctl` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

## [0.1.0a4] - 2026-08-06

### Fixed

- `cyl download` no longer fails the rest of a long run once the login's token is refreshed.
  A large experiment would download for about an hour and then fail every remaining frame with
  a 404 `Bucket not found` — a message that reads like missing data (one report: 36,481 frames
  downloaded, then 23,855 consecutive failures). The cause was not the session expiring:
  `supabase-py` refreshes the token on its own timer. The cause was resolving the storage
  bucket handle **once** and reusing it for the whole run — a bucket handle captures the
  auth header when it is created, so the cached one kept presenting the token it was born
  with while the client refreshed around it. The handle is now resolved per download, which
  costs nothing and picks the new token up. A request that still fails for an expired or
  transient reason is retried once; a genuinely missing object fails immediately rather than
  doubling the request count across an experiment. When a request does fail for good, the
  error names a possible expired session instead of claiming the bucket is missing.
- `cyl download` refuses to start when two frames would be written to the same file. Frame
  paths are built from `wave_number`/`plant_age_days`/`date_scanned`/`qr_code`/`frame_number`,
  all of which are nullable — and Postgres unique constraints do not collide NULLs — so two
  rows could map to one filename. The second frame was silently never fetched and reported as
  already present. `cyl download-for-predict` already guarded this on the same data.
- Frame paths built from database fields can no longer escape the output directory (a value
  containing `../`, or an absolute `frame_number`, previously steered the write elsewhere).
- Downloaded frames are written `0644` again rather than `0600`, so they stay readable to
  collaborators on a shared directory alongside `scans.csv`.
- `download_log.txt` now reports a scan whose frame list could not be fetched as `UNLISTED`,
  in its proper scan position, and counts it separately — it used to appear as a single
  failed frame at the bottom of the log, understating how much data was actually missing.
- A run sweeps `.dl-*.tmp` files left behind by a previously killed run.

### Added

- `cyl download` downloads frames **concurrently** instead of one at a time, which is the
  difference between a large cylinder experiment taking hours and taking a fraction of that.
  The per-frame Storage round-trips are I/O-bound, so they overlap well across a thread pool.
  `-n`/`--workers` sets the width (default 8, range 1-64; `1` restores the old sequential
  behaviour). The ceiling is enforced in `download_images()` as well as at the flag, so no
  caller can spawn an unbounded pool, and work is submitted in a bounded window so a
  400k-frame experiment doesn't queue every task up front. Results keep scan/frame order, so
  `download_log.txt` stays deterministic regardless of how the pool interleaves the work.
- `cyl download` is resumable: a frame already written to the output directory is kept and
  reported as `already on disk` (logged as `SKIP`), so re-running the same command after any
  interruption — a failed run, a killed process, a network drop — fetches only what is still
  missing instead of restarting from zero. Frames are written atomically, so a crash
  mid-write can't leave a truncated file for a later run to mistake for complete. Pass
  `--overwrite` to re-fetch everything regardless.

  A download records which selection it fetched, so re-running the same command resumes it and
  downloading a different selection into the same directory is refused — two selections in one
  tree would leave `scans.csv` describing only the newer one. Use a directory per selection.

  **Note for output directories written by 0.1.0a3 or earlier**: those releases wrote frames
  non-atomically, so an interrupted run could leave a truncated file behind. Resume treats any
  non-empty file as complete, so re-fetch such a directory once with `--overwrite`.

## [0.1.0a3] - 2026-08-04

### Changed

- Docs: `README.md` and the PyPI landing page (`README.pypi.md`) reworked for usability —
  a **Quickstart**, a **Common conventions** section (profiles, `--output`, interactive menus,
  read/write roles), a full **`cyl download`** section (incl. `--experiment-name`), and a
  **"Finding what to download"** walkthrough with real examples for the read commands.
- `cyl datasets list` now takes `--output [csv|json]` (with `--json` kept as an alias for
  `--output json`) — the standard machine-output selector, adding CSV export to the
  `0.1.0a2` command. `datasets get` keeps `--json` (it returns a single object with a
  nested traits array, which CSV can't represent); `datasets create` is unaffected.

### Added

- `bloomctl cyl download` can select the experiment by name: `--experiment-name
  "<text>"` resolves a single experiment by a case-insensitive substring match on its
  name, then downloads it; `--species` narrows an ambiguous name. The match runs
  server-side via the new `cyl_experiment_search` RPC (a `SECURITY INVOKER` function
  taking the query as a bound parameter, trigram-indexed so it scales), so the CLI
  never fetches the whole table and the query can't alter the SQL. The typed
  `--experiment-id` / `--scan-id` paths are unchanged. An ambiguous name lists the
  candidate experiments (id · name · species · created) and exits without
  downloading, so a pipeline never fetches a guessed experiment.
- `bloomctl cyl accessions list` / `sample-counts` — new commands (no `0.1.0a2`
  equivalent), reading the server-side `cyl_experiment_accessions` /
  `cyl_accession_sample_counts` views. `accessions list` lists the accessions used in an
  experiment — `--experiment-id N` (scriptable) or an experiment menu when it is omitted
  (hybrid). `accessions sample-counts` shows the plant count per accession per species —
  `--species NAME` filters directly (scriptable) or `--species-menu` picks from a numbered
  menu of species that have accessions (0 = All); the two are mutually exclusive, and
  omitting both means all species. Both support `--output csv|json`; menus write to stderr
  so `--output` on stdout stays machine-clean.
- `bloomctl cyl qc list-sets` — new command (no `0.1.0a2` equivalent). List cylinder QC
  sets (name, species, experiment, QC-code count) for live experiments (`--include-deleted`
  also shows sets on soft-deleted experiments), sorted by species then experiment.
  `--output csv|json` for machine-readable output.
- `bloomctl cyl datasets list` gains a `--experiment` menu flag: pick an experiment
  that has datasets (0 = All) from a numbered menu. `--experiment-id N` stays for
  scripts, and `datasets list` with neither still lists all datasets. Menu on stderr.
- `bloomctl cyl experiments list` gains **species selection** and **output formats**.
  `--species NAME` filters to a species by common name (scriptable), and `--species-menu`
  picks one from a numbered menu ("All species" + one per species that has experiments;
  needs a terminal); the two are mutually exclusive and omitting both lists all species.
  This follows the standard species selector — `--species` is always a typed value and
  `--species-menu` is always the picker, matching `accessions sample-counts` (and the
  typed `--species` on `download --experiment-name`). `--output csv|json` (default is the
  table; `--json` is an alias for `--output json`) makes it easy to pull an `experiment_id`
  for `cyl download`, and `--limit` caps the fetch so the query is never unbounded (and
  warns on stderr when the cap is hit, since the newest experiments are dropped first).
- `bloomctl cyl batch-download-for-predict <out_dir>` — stage a batch of cylinder
  scans in one invocation. Reads scan_ids from `--scan-ids-file` (a JSON array,
  path or `-` for stdin) or `--scan-ids 1,2,3` (comma-separated, mutually
  exclusive with `--scan-ids-file`); stages each into the same nested
  `out_dir/{scan_key}/` layout `cyl download-for-predict` writes for one scan.
  Isolates per-scan failures (one bad scan doesn't abort the batch) and skips a
  scan whose stage directory already has a valid sidecar (resume). Supports
  `--json` for a machine-readable per-scan report; exits non-zero if any scan
  failed, zero on empty input or all ok/skipped. For the A4 per-batch pipeline's
  `download-all` stage (#529).
- `bloomctl cyl batch-ingest-result <envelopes_dir>` — write back a batch of
  per-scan `ResultEnvelope`s in one invocation. Ingests every
  `{scan_key}.result.json` file directly under `envelopes_dir` (the flat layout
  `trait_extractor.extract_batch`'s output produces) via the same
  validation + RPC path as `cyl ingest-result`. Isolates per-envelope failures
  (one bad envelope doesn't abort the batch); a no-op re-delivery is reported
  distinctly from a real failure. Accepts an optional `--predictions-dir`
  (predict's own nested batch output root) to construct and upload blobs per
  envelope, reusing `cyl ingest-result --predictions-dir`'s logic unchanged.
  Supports `--json`; exits non-zero if any envelope failed, zero on empty input
  or all ok/skipped. For the A4 per-batch pipeline's `write-back` stage (#529).

### Fixed

- `build_sidecar()` (shared by `cyl download-for-predict` and
  `cyl batch-download-for-predict`) now writes `image_ids` as `str`, matching
  the `image_ids: list[str]` shape `sleap_roots_contracts.InputRef` and, in
  turn, `trait_extractor`'s `ScanMetadata` require. Previously wrote raw ints
  straight from the DB row, so every real predict→traits run failed
  `ScanMetadata` validation 100% of the time — masked until now because prior
  tests only ever fed hand-authored fixture sidecars with correct string ids,
  never a real `bloomctl`-produced one. `image_ids`/`images_checksum` are now
  constructed via `InputRef` itself rather than a bare dict, so Pydantic
  catches this class of type mismatch at construction time going forward.
  `scan_is_already_staged()` (the `batch-download-for-predict` resume/skip
  check) now also rejects a sidecar whose `image_ids` aren't all `str`, so a
  scan staged by the pre-fix `build_sidecar()` gets re-staged with corrected
  ids instead of being skipped forever (#555).

## [0.1.0a2] - 2026-07-23

### Changed

- Commands are now grouped by data type: `bloomctl download` moved under the
  `cyl` group as `bloomctl cyl download` (both cylinder commands now live in
  `bloomctl.cyl`, one file per command), matching the legacy CLI's layout (#433).

### Added

- `bloomctl cyl datasets list` / `get` / `create` — list cylinder trait datasets (with
  `--experiment-id` filter and `--json` output), show one dataset's details plus its
  unique traits (`get <name>`, via the `cyl_dataset_trait_names` view), and create one
  via the `create_cyl_dataset` RPC (`--qc-set-name`,
  `--timepoints`). Ports the legacy `cyl datasets` commands (`list`/`create`) and adds `get`.
- `bloomctl cyl experiments list` — list cylinder experiments (species, name, id),
  sorted by species then name, with `--json` output. Ports the legacy
  `cyl experiments list` command.
- `bloomctl cyl ingest-result <envelope>` — write a per-scan `ResultEnvelope`
  back to Bloom via the `insert_cyl_result_envelope` RPC. Reads from a path or
  stdin (`-`), validates against `sleap-roots-contracts`, sends the original JSON
  (preserving the producer's `idempotency_key`), reports the first-writer-wins
  no-op distinctly from an error, maps RPC validation failures to actionable
  messages, and supports `--json` output (#397).
- `bloomctl cyl ingest-result --predictions-dir DIR` — construct and upload the
  envelope's `blobs`: reads predict's `{scan_key}.predictions.json`
  (`PredictionArtifact`/`PredictionManifest`, promoted into
  `sleap-roots-contracts` v0.1.0a5 for this), verifies each `.slp`'s checksum,
  uploads to the new `cyl-intermediates` storage bucket (idempotent per-blob;
  fails fast before any upload or RPC call on a missing/malformed manifest,
  missing file, checksum mismatch, or conflicting existing blob). Bumped the
  `sleap-roots-contracts` floor to `>=0.1.0a5` (#407).
- `bloomctl cyl download-for-predict <scan-id> <out>` — stage one cylinder scan
  into the layout `sleap_roots_predict.discover_scans` expects (frames beside a
  `scan_metadata.json` sidecar authored from live DB metadata), for A4 per-scan
  pipeline stage-in. Distinct from `cyl download`'s legacy `images/Wave{n}/…` +
  `scans.csv` layout (#411).
- `bloomcli/Dockerfile` + GHCR publishing — `bloomctl` is now built as a
  container image from monorepo source and published to
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl` (`sha-<short>` on every
  `staging` push, `staging` mutable tag, a matching version tag per GitHub
  Release). PR-time Dockerfile validation + CVE scanning ride
  `pr-checks.yml`'s existing `docker-build` job; the publishing workflow
  itself is push-only.

## [0.1.0a1] - 2026-06-30

### Added

- Initial pre-release of the Python `bloomctl`, successor to the Node
  `@salk-hpi/bloom-cli` (#347).
- `bloomctl login` — bootstraps client config from the Bloom server
  `/api/client-info` endpoint and authenticates, storing credentials per profile.
- Credential management (`--profile`, `--server`, `--api-url`, `--anon-key`).
