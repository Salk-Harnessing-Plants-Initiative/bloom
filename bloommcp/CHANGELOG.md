# Changelog

All notable changes to `bloommcp` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

## [0.1.0a1] - 2026-08-14

### Added

- First PyPI-publishable pre-release. Packaging mechanics (`pyproject.toml` metadata,
  `[build-system]`, the `bloom-mcp` entry point, Docker mode, and the CI wheel-build/import
  smoke test) were already in place; this release adds `bloom_mcp.__version__` and a
  `bloom-mcp --version`/`-V` flag, this changelog, and the release pipeline
  (`version-bloommcp.yml` / `release-bloommcp.yml`) that publishes it (#663).
