# Changelog

All notable changes to `bloomctl` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

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
