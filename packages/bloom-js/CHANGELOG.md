# Changelog

All notable changes to `@salk-harnessing-plants-initiative/bloom-js` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/) (pre-releases are
published as `X.Y.Z-dev.N` under the `dev` dist-tag).

## [Unreleased]

## [0.4.0]

### Added

- GraviScan: `insertGraviImageMetadata`, `updateGraviImageMetadata`, `getAllGraviScanners`, `insertGraviScanSession` and `insertGraviScanMetadata`, with the types `GraviImageMetadata`, `GraviScannersRow`, `GraviScanSessionParams` and `GraviScanMetadataParams`. Ported from the V1 GitLab packages (`@salk-hpi/bloom-js` 0.3.0-dev.4).
- `uploadRawFile` uploads a file unchanged and returns its SHA-256 and size; `uploadJpegImage` uploads as JPEG.
- First unit tests.

### Fixed

- Cylinder images record their camera settings again (`exposure_time`, `gain`, `brightness`, `contrast`, `gamma`, `seconds_per_rot`): `insertImageMetadata` calls `insert_image_v3_0`, as in V1. 0.3.0 called `insert_image_v2_0`, which dropped them. Missing settings are stored as `0`.

## [0.3.0]

### Added

- First release on GitHub Packages under the `@salk-harnessing-plants-initiative` scope. Earlier versions were published as `@salk-hpi/bloom-js` on the V1 GitLab registry.
