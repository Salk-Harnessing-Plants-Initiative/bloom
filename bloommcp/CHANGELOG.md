# Changelog

All notable changes to `bloommcp` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [PEP 440](https://peps.python.org/pep-0440/) versioning
(pre-releases are published to PyPI as `aN`/`bN`/`rcN`).

## [Unreleased]

### Added

- Salk connector: with `BLOOMMCP_TUNNEL_SECRET` set, a request from Caddy's tunnel entrance is
  admitted for the Bloom account matching the `email` in AIRC's verified JWT, and usage is recorded under
  that account. Startup fails on a secret under 32 characters, or without `BLOOMMCP_API_KEY`
  or the JWT's keys URL, issuer and audience.
  A refused POST gets a JSON-RPC error carrying the reason, so MCP clients can show it.

### Removed

- The public route at `https://bloom.salk.edu/bloommcp` (and staging's `/bloommcp`). bloommcp is
  reached only through the Salk connector now. If you added it to Claude Code, remove it with
  `claude mcp remove bloommcp-prod` (or `bloommcp-staging`) and use the Salk connector instead.
- bloommcp's own Bloom login: it no longer accepts a Bloom OAuth access token or serves the
  OAuth discovery document, and `BLOOMMCP_PUBLIC_URL` (now local mode only),
  `BLOOMMCP_OAUTH_AUTHORIZATION_SERVER` and `BLOOMMCP_OAUTH_JWKS_URI` no longer configure a login.
  Usage is recorded from `X-Bloom-Identity` or the Salk connector only.

## [0.1.0a1] - 2026-09-02

### Added

- First PyPI-publishable pre-release. Packaging mechanics (`pyproject.toml` metadata,
  `[build-system]`, the `bloom-mcp` entry point, Docker mode, and the CI wheel-build/import
  smoke test) were already in place; this release adds `bloom_mcp.__version__` and a
  `bloom-mcp --version`/`-V` flag, this changelog, and the release pipeline
  (`version-bloommcp.yml` / `release-bloommcp.yml`) that publishes it (#663).
