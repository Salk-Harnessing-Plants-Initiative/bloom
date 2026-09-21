# Changelog

All notable changes to `bloommcp` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

### Fixed

- `SupabaseStorageBackend.list_prefix` no longer silently truncates a listing at 100 immediate
  children. It made a single unpaginated `client.list(prefix)` call, inheriting the storage
  client's default limit, so a larger prefix returned a short list that read as complete — and
  `read_manifest` gates the whole manifest read on a `list_prefix` membership test, while both
  audit scripts sweep the `bloommcp_output/` root prefix, whose children cross 100 at roughly
  7 experiments. It now pages with an explicit `limit`/`offset` and a pinned sort order,
  de-duplicates across page boundaries, and raises rather than returning a partial listing if
  the backend ignores pagination (#396).

## [0.1.0a1] - 2026-09-02

### Added

- First PyPI-publishable pre-release. Packaging mechanics (`pyproject.toml` metadata,
  `[build-system]`, the `bloom-mcp` entry point, Docker mode, and the CI wheel-build/import
  smoke test) were already in place; this release adds `bloom_mcp.__version__` and a
  `bloom-mcp --version`/`-V` flag, this changelog, and the release pipeline
  (`version-bloommcp.yml` / `release-bloommcp.yml`) that publishes it (#663).
