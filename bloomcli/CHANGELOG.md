# Changelog

All notable changes to `bloomcli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

## [0.1.0a1] - 2026-06-30

### Added

- Initial pre-release of the Python `bloomcli`, successor to the Node
  `@salk-hpi/bloom-cli` (#347).
- `bloomcli login` — bootstraps client config from the Bloom server
  `/api/client-info` endpoint and authenticates, storing credentials per profile.
- Credential management (`--profile`, `--server`, `--api-url`, `--anon-key`).
