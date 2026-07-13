# Changelog

All notable changes to `bloomctl` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

### Changed

- Commands are now grouped by data type: `bloomctl download` moved under the
  `cyl` group as `bloomctl cyl download` (both cylinder commands now live in
  `bloomctl.cyl`, one file per command), matching the legacy CLI's layout.

### Added

- `bloomctl cyl datasets list` / `bloomctl cyl datasets create` — list cylinder trait
  datasets (with `--experiment-id` filter and `--json` output) and create one via the
  `create_cyl_dataset` RPC (`--qc-set-name`, `--timepoints`). Ports the legacy
  `cyl datasets` commands.
- `bloomctl cyl ingest-result <envelope>` — write a per-scan `ResultEnvelope`
  back to Bloom via the `insert_cyl_result_envelope` RPC. Reads from a path or
  stdin (`-`), validates against `sleap-roots-contracts`, sends the original JSON
  (preserving the producer's `idempotency_key`), reports the first-writer-wins
  no-op distinctly from an error, maps RPC validation failures to actionable
  messages, and supports `--json` output (#397).

## [0.1.0a1] - 2026-06-30

### Added

- Initial pre-release of the Python `bloomctl`, successor to the Node
  `@salk-hpi/bloom-cli` (#347).
- `bloomctl login` — bootstraps client config from the Bloom server
  `/api/client-info` endpoint and authenticates, storing credentials per profile.
- Credential management (`--profile`, `--server`, `--api-url`, `--anon-key`).
