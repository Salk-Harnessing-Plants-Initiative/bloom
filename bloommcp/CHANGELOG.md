# Changelog

All notable changes to `bloommcp` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

### Added

- Read-time guard on the manifest `storage_backend` sentinel (#573): reading a
  catalog written by a different backend than the active one raises
  `ManifestBackendMismatchError` (`bloom_mcp.manifest`), surfaced by the result
  store as `CatalogBackendMismatchError` and by the experiment readers as
  `ForeignCatalogError` — all subclasses of the error types consumer tools
  already declare. New env var `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST`
  (unset/`0`/`1`, boot-validated) downgrades the **read** failure to a per-read
  warning for deliberate offline inspection; the write path
  (`create_run`/`commit`) refuses a foreign catalog unconditionally. See
  `docs/storage-backends.md`.

### Changed

- Reads over a foreign catalog — previously served silently, with `qc_clean`'s
  `require_clean` contract and `pca_analysis` accepting whichever backend's
  "latest" they were pointed at — now fail closed with a message naming both
  backends. Single-backend usage (the documented contract since #395) is
  unaffected.

## [0.1.0a1] - 2026-09-02

### Added

- First PyPI-publishable pre-release. Packaging mechanics (`pyproject.toml` metadata,
  `[build-system]`, the `bloom-mcp` entry point, Docker mode, and the CI wheel-build/import
  smoke test) were already in place; this release adds `bloom_mcp.__version__` and a
  `bloom-mcp --version`/`-V` flag, this changelog, and the release pipeline
  (`version-bloommcp.yml` / `release-bloommcp.yml`) that publishes it (#663).
