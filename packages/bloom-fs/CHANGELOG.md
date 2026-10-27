# Changelog

All notable changes to `@salk-harnessing-plants-initiative/bloom-fs` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/) (pre-releases are
published as `X.Y.Z-dev.N` under the `dev` dist-tag).

## [Unreleased]

## [0.4.0]

### Added

- `CylImageMetadata` has optional cylinder camera settings (`num_frames`, `exposure_time`, `gain`, `brightness`, `contrast`, `gamma`, `seconds_per_rot`) for callers that build it themselves, such as Bloom Desktop. Folder uploads (`getImageMetadata`) have no source for them and send `0`. Ported from the V1 GitLab packages (`@salk-hpi/bloom-fs` 0.3.0-dev.3).
- First unit tests.

## [0.3.0]

### Added

- First release on GitHub Packages under the `@salk-harnessing-plants-initiative` scope. Earlier versions were published as `@salk-hpi/bloom-fs` on the V1 GitLab registry.
