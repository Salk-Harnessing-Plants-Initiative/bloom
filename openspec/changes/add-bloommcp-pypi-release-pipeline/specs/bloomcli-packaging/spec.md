## ADDED Requirements

### Requirement: Release Workflow Skips Foreign Package Tags

`release-bloomcli.yml`'s `validate-release` job SHALL carry a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloomctl-')` —
so the workflow skips cleanly (no failing run) when a published Release's tag does not start
with `bloomctl-`, while a `workflow_dispatch` run (which has no release tag) always passes the
guard. Because GitHub Actions skip-propagates through `needs:`, gating only `validate-release`
is sufficient to also skip `build-and-publish` — the guard is not duplicated on every job.

This narrows `release-bloomcli.yml`'s previously-documented — but never actually used in a
real release — support for a bare `vX.Y.Z` or `X.Y.Z` release tag (in addition to
`bloomctl-vX.Y.Z`) down to the `bloomctl-vX.Y.Z` form only. `bloomcli/RELEASE_PROCESS.md`
SHALL be updated to document only the `bloomctl-vX.Y.Z` tag form going forward.

#### Scenario: A bloommcp release does not fail bloomcli's workflow

- **WHEN** a GitHub Release is published tagging `bloommcp-v<X>`
- **THEN** `release-bloomcli.yml`'s `validate-release` job (and, transitively via `needs:`,
  `build-and-publish`) is skipped rather than run and failed

#### Scenario: workflow_dispatch is unaffected by the tag guard

- **WHEN** `release-bloomcli.yml` is run via `workflow_dispatch` (no release tag present)
- **THEN** the tag-prefix guard does not skip the run

#### Scenario: A previously-documented bare-version tag form is no longer accepted

- **WHEN** a GitHub Release is published tagging a bare `vX.Y.Z` or `X.Y.Z` (no `bloomctl-`
  prefix)
- **THEN** the workflow's jobs are skipped rather than validated or published — a deliberate
  narrowing of the previously-documented, but never actually used, alternate tag forms
