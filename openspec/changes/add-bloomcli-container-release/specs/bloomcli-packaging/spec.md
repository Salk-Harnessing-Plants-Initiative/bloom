## ADDED Requirements

### Requirement: Container Image Buildable From Monorepo Source

`bloomcli/` SHALL have a `Dockerfile` that builds a runnable `bloomctl` container image
directly from the monorepo source at the commit being built, without depending on that
commit's version having been published to PyPI. The image SHALL install only bloomctl's
declared dependencies (no native build toolchain / apt packages), and SHALL run as a
non-root user.

#### Scenario: Image builds and runs from source at any commit

- **GIVEN** a checkout of the monorepo at any commit on `staging`
- **WHEN** `docker build -f bloomcli/Dockerfile bloomcli/` (or the equivalent CI step
  with `context: ./bloomcli`) is run
- **THEN** the build succeeds without installing `bloomctl` from PyPI
- **AND** `docker run <image> --version` prints a version string and exits 0

#### Scenario: No native build toolchain is installed

- **WHEN** `bloomcli/Dockerfile` is parsed
- **THEN** it contains no `apt-get install` step

#### Scenario: Container runs as a non-root user

- **WHEN** `bloomcli/Dockerfile` is parsed
- **THEN** it declares a `USER` instruction naming a non-root user before the final
  `ENTRYPOINT`

### Requirement: GHCR Image Publishing for bloomctl

CI MUST build the `bloomctl` container image on every push to `staging` that touches
`bloomcli/**`, and push it to `ghcr.io/salk-harnessing-plants-initiative/bloomctl` with
both an immutable `sha-<short-git-sha>` tag and the mutable `staging` tag. CI MUST also
build and push on a published GitHub Release (adding the release's semver tag) and on
`workflow_dispatch`. CI MUST build (but never push) on a pull request that touches
`bloomcli/**`. CI MUST NOT build on push to `main`.

#### Scenario: Push to staging builds and pushes both tags

- **GIVEN** a commit touching `bloomcli/**` is pushed to `staging`
- **WHEN** the `docker-build-bloomcli.yml` workflow runs
- **THEN** `ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-<short>` and
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl:staging` both exist and resolve to
  the same digest

#### Scenario: Pull request builds without pushing

- **GIVEN** a pull request modifies a file under `bloomcli/**`
- **WHEN** the workflow runs
- **THEN** the image is built
- **AND** no push to `ghcr.io` occurs

#### Scenario: Published Release adds a semver tag

- **GIVEN** a GitHub Release tagged `bloomctl-v0.1.0a2` is published
- **WHEN** the workflow runs
- **THEN** `ghcr.io/salk-harnessing-plants-initiative/bloomctl:0.1.0a2` exists

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
existing tag/changelog validation without modification.

#### Scenario: Changelog entry exists for the bumped version

- **GIVEN** `bloomcli/pyproject.toml` declares version `0.1.0a2`
- **WHEN** `bloomcli/CHANGELOG.md` is checked
- **THEN** it contains a `## [0.1.0a2]` heading
