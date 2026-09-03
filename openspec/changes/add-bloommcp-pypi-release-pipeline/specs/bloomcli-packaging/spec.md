## ADDED Requirements

### Requirement: Release Workflow Skips Foreign Package Tags

`release-bloomcli.yml`'s `validate-release` job SHALL carry a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloomctl-')` —
so the workflow skips cleanly (no failing run) when a published Release's tag does not start
with `bloomctl-`, while a `workflow_dispatch` run (which has no release tag) always passes the
guard. Because GitHub Actions skip-propagates through `needs:`, gating only `validate-release`
is sufficient to also skip `build-and-verify` and `build-and-publish` — the guard is not
duplicated on every job.

A separate `release-tag-guard.yml` workflow SHALL run on every published Release (no job-level
skip) and fail if the tag matches neither `bloomctl-` nor `bloommcp-` — so a typo'd or unknown
tag prefix, which would otherwise make both packages' `release-*.yml` workflows skip cleanly at
once, still produces a visible, failing Actions run.

This narrows `release-bloomcli.yml`'s previously-documented — but never actually used in a
real release — support for a bare `vX.Y.Z` or `X.Y.Z` release tag (in addition to
`bloomctl-vX.Y.Z`) down to the `bloomctl-vX.Y.Z` form only. `bloomcli/RELEASE_PROCESS.md`
SHALL be updated to document only the `bloomctl-vX.Y.Z` tag form going forward.

#### Scenario: A bloommcp release does not fail bloomcli's workflow

- **WHEN** a GitHub Release is published tagging `bloommcp-v<X>`
- **THEN** `release-bloomcli.yml`'s `validate-release` job (and, transitively via `needs:`,
  `build-and-verify` and `build-and-publish`) is skipped rather than run and failed

#### Scenario: workflow_dispatch is unaffected by the tag guard

- **WHEN** `release-bloomcli.yml` is run via `workflow_dispatch` (no release tag present)
- **THEN** the tag-prefix guard does not skip the run

#### Scenario: A previously-documented bare-version tag form is no longer accepted

- **WHEN** a GitHub Release is published tagging a bare `vX.Y.Z` or `X.Y.Z` (no `bloomctl-`
  prefix)
- **THEN** the workflow's jobs are skipped rather than validated or published — a deliberate
  narrowing of the previously-documented, but never actually used, alternate tag forms

#### Scenario: An unknown tag prefix fails loudly instead of double-skipping silently

- **WHEN** a GitHub Release is published tagging something matching neither `bloomctl-` nor
  `bloommcp-` (e.g. a typo like `bloomcp-v1.0.0`)
- **THEN** both `release-bloomcli.yml` and `release-bloommcp.yml` skip cleanly, but
  `release-tag-guard.yml` runs and fails, so at least one Actions run visibly reports that
  nothing was published

## MODIFIED Requirements

### Requirement: GHCR Image Publishing for bloomctl

CI MUST build the `bloomctl` container image on every push to `staging` that touches
`bloomcli/**`, and push it to `ghcr.io/salk-harnessing-plants-initiative/bloomctl` with
both an immutable `sha-<short-git-sha>` tag and the mutable `staging` tag. CI MUST also
build and push on a published GitHub Release, adding a tag equal to the release's bare
version (derived by stripping any `bloomctl-v`/`v` prefix from the release tag — never
via strict-semver parsing, since bloomctl's versions are PEP 440, not semver), and on
`workflow_dispatch`. This publishing workflow MUST NOT trigger on a pull request (PR-time
validation is a separate requirement, above) and MUST NOT trigger on push to `main`. On
the `release` trigger, the workflow MUST verify the release tag's stripped version
matches `bloomcli/pyproject.toml`'s actual version before pushing any image, so a
mismatched release tag cannot produce a mistagged GHCR image.

On the `release` trigger, the tag-validation job SHALL additionally skip cleanly (not run,
not fail) when the release tag does not start with `bloomctl-` — this workflow reacts to
every published Release regardless of which monorepo package it is for, but only ever
builds and pushes the `bloomctl` image, so a Release for a different package (e.g.
`bloommcp-vX.Y.Z`) MUST NOT produce a failing run here (#663 review).

#### Scenario: Push to staging builds and pushes both tags

- **GIVEN** a commit touching `bloomcli/**` is pushed to `staging`
- **WHEN** the `docker-build-bloomcli.yml` workflow runs
- **THEN** `ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-<short>` and
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl:staging` both exist and resolve to
  the same digest

#### Scenario: Published Release adds a version tag matching the release, not a semver-parsed tag

- **GIVEN** a GitHub Release tagged `bloomctl-v0.1.0a2` is published
- **WHEN** the workflow runs
- **THEN** `ghcr.io/salk-harnessing-plants-initiative/bloomctl:0.1.0a2` exists (the
  `bloomctl-v` prefix stripped, matching `release-bloomcli.yml`'s own tag-parsing logic)
- **AND** no strict-semver tag-matching rule is relied upon to produce this tag
- **AND** the mutable `staging` tag is NOT modified (a Release may be tagged from a
  commit other than `staging`'s current tip)

#### Scenario: A release tag that doesn't match pyproject.toml's version does not push a mistagged GHCR tag

- **GIVEN** a GitHub Release is published with a tag whose stripped version does not
  equal `bloomcli/pyproject.toml`'s current version
- **WHEN** the workflow runs
- **THEN** no image is pushed to `ghcr.io/salk-harnessing-plants-initiative/bloomctl`
  under the mismatched version tag
- **AND** the workflow run fails with an error naming both the tag-derived version and
  the `pyproject.toml` version

#### Scenario: A Release for a different monorepo package does not fail this workflow

- **GIVEN** a GitHub Release is published tagging `bloommcp-v<X>` (or any tag not
  starting with `bloomctl-`)
- **WHEN** the `docker-build-bloomcli.yml` workflow runs
- **THEN** its `validate-tag` job is skipped rather than run and failed
- **AND** `build-and-push` is also skipped (it only runs on `release` when `validate-tag`
  actually succeeded), so no image is built or pushed

#### Scenario: docker-build-bloomcli.yml never triggers on a pull request

- **GIVEN** a pull request modifies a file under `bloomcli/**`
- **WHEN** GitHub Actions evaluates triggers
- **THEN** `docker-build-bloomcli.yml` does not run (only `pr-checks.yml`'s
  `docker-build` job runs, per the pre-merge-validation requirement above)

#### Scenario: Push to main does not trigger a build

- **GIVEN** a commit is pushed to `main`
- **WHEN** GitHub Actions evaluates triggers
- **THEN** `docker-build-bloomcli.yml` does not run

#### Scenario: A change outside bloomcli/ does not trigger a build on staging

- **GIVEN** a commit pushed to `staging` touches no path under `bloomcli/`
- **WHEN** GitHub Actions evaluates triggers
- **THEN** `docker-build-bloomcli.yml` does not run
