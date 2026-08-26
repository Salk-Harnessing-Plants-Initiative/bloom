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
`workflow_dispatch`, as three jobs: `validate-release` → `build-and-verify` →
`build-and-publish`. `validate-release` SHALL validate — on a real Release only — that
the release tag's version matches `bloommcp/pyproject.toml` and that a matching
`CHANGELOG.md` entry exists, then lint and test the package, failing before any build step if
any of these checks fail. `build-and-verify` SHALL then build the package, record the built
artifact's checksum, run `twine check`, verify the built wheel imports — every `bloom_mcp`
submodule via an exhaustive `pkgutil.walk_packages` walk, run twice (once at default
resolution, once with `--prerelease=allow`, so a broken transitive pre-release is caught before
a real user hits it), plus explicitly that `bloom_mcp.server.build_app()` succeeds and that the
concrete Supabase-backed adapters — `bloom_mcp.data_access.SupabaseReader`,
`bloom_mcp.result_store.SupabaseResultStore` — and their `postgrest`/`supabase` transitive
imports resolve, since these sit behind `main()`'s composition root and are not exercised by
`build_app()` alone — and verify the entry point from an isolated, project-free environment:
`bloom-mcp --version` returns cleanly, the installed `bloom-mcp` console script resolves to
`bloom_mcp.server:main`, and a real invocation with no environment configured fails fast
(raises, does not hang) rather than silently starting the server. `build-and-verify` SHALL
then upload the built artifact — all before any job holds the publish credential.
`build-and-publish` SHALL hold the OIDC token and the `pypi` environment,
run no other third-party code, download the verified artifact, re-check its checksum, and
publish to PyPI via trusted publishing (OIDC, no stored token) only when triggered by a
published Release; a `workflow_dispatch` run SHALL stop after verification without publishing.

`build-and-verify` and `validate-release` SHALL NOT hold `id-token: write` or the `pypi`
environment — only `build-and-publish` may, since `build-and-verify` deliberately executes
third-party code (the build backend, `twine`, the freshly built wheel's own dependency chain)
that must never run alongside the publish credential.

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

#### Scenario: The publish credential never shares a job with third-party code

- **WHEN** `release-bloommcp.yml` runs, for any trigger
- **THEN** the job that builds the package and runs `twine check`/wheel-import checks
  (`build-and-verify`) holds neither `id-token: write` nor the `pypi` environment
- **AND** only the job that holds them (`build-and-publish`) runs no code beyond
  downloading the pre-verified artifact, re-checking its checksum, and publishing it

#### Scenario: A broken transitive pre-release fails the build before publish

- **WHEN** a transitive dependency has a pre-release version that removes or breaks an API
  `bloom_mcp` (or one of its Supabase adapters) imports
- **THEN** the `--prerelease=allow` wheel-import pass in `build-and-verify` fails, blocking the
  release before any publish step runs — even though the default-resolution pass on the same
  build would have passed

#### Scenario: A real invocation with no environment fails fast, not silently

- **WHEN** the built wheel's `bloom-mcp` console script is invoked with no `BLOOM_*`/
  `SUPABASE_*` environment variables set, and no `--version`/`-V` flag
- **THEN** it raises before binding a port or starting the server, within a bounded time —
  neither succeeding nor hanging

### Requirement: Release Workflow Skips Foreign Package Tags

`release-bloommcp.yml`'s `validate-release` job SHALL carry a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloommcp-')` —
so the workflow skips cleanly (no failing run) when a published Release's tag does not start
with `bloommcp-`, while a `workflow_dispatch` run (which has no release tag) always passes the
guard. Because GitHub Actions skip-propagates through `needs:` (transitively, across both hops
of `validate-release` → `build-and-verify` → `build-and-publish`), gating only
`validate-release` is sufficient to also skip the other two jobs — the guard is not
duplicated on every job.

The symmetric guard on the existing `release-bloomcli.yml` (`bloomctl-` prefix) is specified
as a delta against the `bloomcli-packaging` capability, since that workflow is bloomcli's, not
bloommcp's.

#### Scenario: A bloomctl release does not fail bloommcp's workflow

- **WHEN** a GitHub Release is published tagging `bloomctl-v<X>`
- **THEN** `release-bloommcp.yml`'s `validate-release` job (and, transitively via `needs:`,
  `build-and-verify` and `build-and-publish`) is skipped rather than run and failed

#### Scenario: workflow_dispatch is unaffected by the tag guard

- **WHEN** `release-bloommcp.yml` is run via `workflow_dispatch` (no release tag present)
- **THEN** the tag-prefix guard does not skip the run

### Requirement: Release Tag Guard Catches Unknown Prefixes

A `release-tag-guard.yml` workflow SHALL run on every published GitHub Release, carry no
job-level skip condition, and fail if the release tag matches neither `bloomctl-` nor
`bloommcp-`. This is a cross-cutting guard specified once here (rather than duplicated per
package) since it does not belong to either package's own release workflow — the reciprocal
statement of this same requirement also appears as a delta against the `bloomcli-packaging`
capability, since `release-tag-guard.yml` is a new top-level file neither existing capability
solely owns.

#### Scenario: A typo'd or unknown tag prefix produces a visible failing run

- **WHEN** a GitHub Release is published tagging something matching neither `bloomctl-` nor
  `bloommcp-` (e.g. `bloomcp-v1.0.0`)
- **THEN** `release-tag-guard.yml` runs (unlike `release-bloomcli.yml`/`release-bloommcp.yml`,
  which both skip cleanly) and fails, naming the tag and the known prefixes it matched neither
  of
