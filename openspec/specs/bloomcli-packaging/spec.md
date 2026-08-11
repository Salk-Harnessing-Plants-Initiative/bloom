# bloomcli-packaging Specification

## Purpose
TBD - created by archiving change add-bloomcli-container-release. Update Purpose after archive.
## Requirements
### Requirement: Container Image Buildable From Monorepo Source

`bloomcli/` SHALL have a `Dockerfile` that builds a runnable `bloomctl` container image
directly from the monorepo source at the commit being built, without depending on that
commit's version having been published to PyPI. The image SHALL install only bloomctl's
declared dependencies (no native build toolchain / apt packages), SHALL run as a
non-root user, and SHALL use a digest-pinned base image and a digest-pinned `uv`
install so the build is reproducible.

#### Scenario: Image builds and runs from source at any commit

- **GIVEN** a checkout of the monorepo at any commit on `staging`
- **WHEN** `docker build -f bloomcli/Dockerfile bloomcli/` (or the equivalent CI step
  with `context: ./bloomcli`) is run
- **THEN** the build succeeds without installing `bloomctl` from PyPI
- **AND** `docker run <image> --version` prints a version string and exits 0

#### Scenario: No native build toolchain is installed

- **WHEN** `bloomcli/Dockerfile` is parsed
- **THEN** it contains no `apt-get install` step

#### Scenario: Base image and uv install are digest-pinned

- **WHEN** `bloomcli/Dockerfile` is parsed
- **THEN** its `FROM` instruction pins the base image with an `@sha256:` digest
- **AND** the `uv` binary is copied in from a digest-pinned source (`COPY --from=...@sha256:...`)

#### Scenario: Container runs as a non-root user and is not a service

- **WHEN** `bloomcli/Dockerfile` is parsed
- **THEN** it declares a `USER` instruction naming a non-root user before the final
  `ENTRYPOINT`
- **AND** the final instruction is an exec-form `ENTRYPOINT` (not a shell-form
  `ENTRYPOINT` and not a bare `CMD`)
- **AND** it contains no `EXPOSE` or `HEALTHCHECK` instruction (this is an
  invoked-per-step CLI image, not a long-running service)

### Requirement: Pre-Merge Dockerfile Validation and CVE Scanning

Every pull request that touches `bloomcli/**` MUST build the `bloomcli` image (without
pushing it to any registry) and scan it for known CVEs, using the same CI job that
already validates every other Bloom custom-service Dockerfile.

#### Scenario: PR builds and scans the image without pushing

- **GIVEN** a pull request modifies a file under `bloomcli/**`
- **WHEN** `pr-checks.yml`'s `docker-build` job runs
- **THEN** the `bloomcli` image is built with `push: false`
- **AND** the built image is scanned for CVEs by the same Trivy step used for the other
  five custom-service images
- **AND** no push to any container registry occurs

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

### Requirement: PyPI Release Readiness

`bloomcli/CHANGELOG.md`'s `[Unreleased]` section SHALL accurately describe every
merged-but-unreleased bloomctl feature before a version bump is made, and
`bloomcli/pyproject.toml`'s version SHALL be bumped to a version whose changelog entry
exists, so that publishing a matching GitHub Release passes `release-bloomcli.yml`'s
existing tag/changelog validation without modification. This SHALL be enforced by a
standing automated check, not a one-time manual verification.

#### Scenario: Changelog entry exists for the bumped version

- **GIVEN** `bloomcli/pyproject.toml` declares version `0.1.0a2`
- **WHEN** `bloomcli/CHANGELOG.md` is checked
- **THEN** it contains a `## [0.1.0a2]` heading

#### Scenario: A version bump with no matching changelog entry fails CI

- **GIVEN** `bloomcli/pyproject.toml`'s version is bumped to a new value
- **AND** no corresponding `## [<version>]` heading is added to `bloomcli/CHANGELOG.md`
- **WHEN** `bloomcli/tests/test_changelog_version_sync.py` runs
- **THEN** the test fails and names the missing version in its failure message

