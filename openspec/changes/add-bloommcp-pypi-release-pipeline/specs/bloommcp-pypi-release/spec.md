## ADDED Requirements

### Requirement: Package Version Introspection

The `bloommcp` package SHALL expose its installed version as `bloom_mcp.__version__`, sourced
via `importlib.metadata.version("bloommcp")` with a fallback sentinel when running from a
source tree that isn't installed as a distribution — the same pattern `bloomctl.__version__`
uses. The `bloom-mcp` entry point (`bloom_mcp.server:main`) SHALL accept a `--version`/`-V`
flag that prints the version and returns before any environment validation or server startup.

#### Scenario: Installed wheel reports its version

- **WHEN** `bloom_mcp` is imported from an installed wheel
- **THEN** `bloom_mcp.__version__` equals the version recorded in that wheel's package
  metadata

#### Scenario: Unbuilt source tree does not crash on import

- **WHEN** `bloom_mcp` is imported without being installed as a distribution (e.g. no matching
  entry in installed package metadata)
- **THEN** `bloom_mcp.__version__` is a sentinel string rather than raising
  `PackageNotFoundError`

#### Scenario: --version exits before server startup

- **WHEN** `bloom-mcp --version` (or `-V`) is invoked, including with no `BLOOM_*` /
  `SUPABASE_*` environment variables set
- **THEN** it prints the version and returns without raising a validation error or binding a
  port

### Requirement: bloommcp Changelog

`bloommcp/CHANGELOG.md` SHALL exist, follow the Keep a Changelog format (matching
`bloomcli/CHANGELOG.md`'s conventions), and carry one `## [version]` entry per published
release, added before that release is cut. The release workflow's changelog gate SHALL fail a
release whose tag's version has no matching entry.

#### Scenario: Missing changelog entry blocks release

- **WHEN** a GitHub Release is published tagging a version with no matching `## [version]`
  entry in `bloommcp/CHANGELOG.md`
- **THEN** `release-bloommcp.yml`'s `validate-release` job fails before any build or publish
  step runs

### Requirement: bloommcp Version Bump Workflow

A manual-dispatch `version-bloommcp.yml` workflow SHALL bump `bloommcp/pyproject.toml`'s
version (via a selected bump type, or a custom version override) and open a pull request that
changes only that file, mirroring `version-bloomcli.yml`.

#### Scenario: Dispatching a bump opens a PR

- **WHEN** `version-bloommcp.yml` is dispatched with a `bump_type` (or a `custom_version`)
- **THEN** it opens a pull request that changes only `bloommcp/pyproject.toml`'s version
  field, with a description naming the previous and new version

### Requirement: bloommcp PyPI Release Workflow

A `release-bloommcp.yml` workflow SHALL run on every published GitHub Release and on manual
`workflow_dispatch`, mirroring `release-bloomcli.yml`'s two-job shape
(`validate-release` → `build-and-publish`). It SHALL validate — on a real Release only — that
the release tag's version matches `bloommcp/pyproject.toml` and that a matching
`CHANGELOG.md` entry exists, then lint and test the package, failing before any build step if
any of these checks fail. `build-and-publish` SHALL then build the package, run
`twine check`, verify the built wheel imports (`bloom_mcp`, `bloom_mcp.tools`,
`bloom_mcp.manifest`, `bloom_mcp.server`; that `bloom_mcp.server.build_app()` succeeds; and
that the concrete Supabase-backed adapters — `bloom_mcp.data_access.SupabaseReader`,
`bloom_mcp.result_store.SupabaseResultStore` — and their `postgrest`/`supabase` transitive
imports resolve, since these sit behind `main()`'s composition root and are not exercised by
`build_app()` alone) and that its `bloom-mcp --version` entry point runs, from an isolated,
project-free environment — all before the publish step. It SHALL publish to PyPI via trusted
publishing (OIDC, no stored token) only when triggered by a published Release; a
`workflow_dispatch` run SHALL stop after verification without publishing.

#### Scenario: Tag/version mismatch blocks release

- **WHEN** a GitHub Release is published tagging `bloommcp-v<X>` where `X` does not equal the
  version in `bloommcp/pyproject.toml`
- **THEN** `validate-release` fails and no build or publish step runs

#### Scenario: A lint or test failure blocks release

- **WHEN** `ruff check` or the `pytest` run fails during `validate-release`
- **THEN** the release fails before any build or publish step runs

#### Scenario: workflow_dispatch never publishes

- **WHEN** `release-bloommcp.yml` runs via `workflow_dispatch`
- **THEN** it builds, checks, and verifies the wheel and its entry point, but the
  publish-to-PyPI step does not run

#### Scenario: A verified release publishes via trusted publishing

- **WHEN** a GitHub Release is published with a matching tag/version and changelog entry, and
  lint/tests/build/twine-check/wheel-import/entry-point checks all pass
- **THEN** `uv publish --trusted-publishing always` runs, with no stored API token anywhere in
  the workflow

### Requirement: Release Workflow Skips Foreign Package Tags

`release-bloommcp.yml`'s `validate-release` job SHALL carry a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloommcp-')` —
so the workflow skips cleanly (no failing run) when a published Release's tag does not start
with `bloommcp-`, while a `workflow_dispatch` run (which has no release tag) always passes the
guard. Because GitHub Actions skip-propagates through `needs:`, gating only `validate-release`
is sufficient to also skip `build-and-publish` — the guard is not duplicated on every job.

The symmetric guard on the existing `release-bloomcli.yml` (`bloomctl-` prefix) is specified
as a delta against the `bloomcli-packaging` capability, since that workflow is bloomcli's, not
bloommcp's.

#### Scenario: A bloomctl release does not fail bloommcp's workflow

- **WHEN** a GitHub Release is published tagging `bloomctl-v<X>`
- **THEN** `release-bloommcp.yml`'s `validate-release` job (and, transitively via `needs:`,
  `build-and-publish`) is skipped rather than run and failed

#### Scenario: workflow_dispatch is unaffected by the tag guard

- **WHEN** `release-bloommcp.yml` is run via `workflow_dispatch` (no release tag present)
- **THEN** the tag-prefix guard does not skip the run
