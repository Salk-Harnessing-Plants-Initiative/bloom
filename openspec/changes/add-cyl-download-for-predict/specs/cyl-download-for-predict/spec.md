## ADDED Requirements

### Requirement: Cyl download-for-predict command stages frames in the predict-ready layout

The `bloomctl` CLI SHALL provide a `cyl download-for-predict <scan-id> <out>` command that
downloads a single cylinder scan's frame files into the layout expected by
`sleap_roots_predict.discover_scans`: all frame files co-located with the sidecar under
`<out>/scan_{scan_id}/`, named `{frame_number}{original_ext}`. The command SHALL accept a
`--profile` / `-p` option (defaulting to the shared default profile). The command SHALL exit
non-zero and print a readable error if the scan does not exist in `cyl_scans_extended`; it SHALL
NOT create a partial output directory in that case.

#### Scenario: Frames written into the nested predict layout

- **WHEN** the user runs `bloomctl cyl download-for-predict 7 /tmp/stage` for a scan that exists
- **THEN** each frame is written to `/tmp/stage/scan_7/<frame_number>{ext}` (co-located with the
  sidecar), and `sleap_roots_predict.discover_scans("/tmp/stage")` returns exactly one
  `ScanInput` with `scan_key="scan_7"` and no `error`

#### Scenario: A successful re-run reconciles stray frame files from an earlier failed attempt

- **WHEN** an earlier failed attempt left a frame file on disk under `scan_{scan_id}/` (e.g. from
  a `cyl_images` row later deleted or renumbered) that a fully successful later run does not
  re-download
- **THEN** the successful run deletes that stray file before writing the sidecar, so every
  image-extension file remaining under `scan_{scan_id}/` corresponds to an entry in the written
  sidecar's `image_ids`

#### Scenario: Scan not found exits non-zero

- **WHEN** the scan-id does not exist in `cyl_scans_extended`
- **THEN** the command exits non-zero with a readable "not found" message and no output directory
  is created

#### Scenario: Frame download failure is surfaced non-zero and no sidecar is written

- **WHEN** one or more frames cannot be downloaded from Storage
- **THEN** the command exits non-zero and reports the failure count; successfully downloaded
  frames remain on disk; the `scan_{scan_id}.scan_metadata.json` sidecar is NOT written, so
  `discover_scans` does not see a partial scan on a later run over the same output directory

#### Scenario: Scan with zero cyl_images rows exits non-zero

- **WHEN** the scan exists in `cyl_scans_extended` but has no `cyl_images` rows
- **THEN** the command exits non-zero with a readable "no frames found" message and no output
  directory is created (treated like a not-found scan, since a frameless stage-in directory is
  never useful to `discover_scans`)

### Requirement: Sidecar scan_key equals the directory name and filename stem

The command SHALL write the sidecar as
`<out>/scan_{scan_id}/scan_{scan_id}.scan_metadata.json` and set the sidecar's `scan_key` field
to `"scan_{scan_id}"`. The `scan_key` field MUST equal the filename stem of the sidecar
(without the `.scan_metadata.json` suffix) because `sleap_roots_predict._load_scan` rejects a
mismatch with an error.

#### Scenario: Sidecar scan_key matches filename stem

- **WHEN** the command writes the sidecar for scan 7
- **THEN** the file is named `scan_7.scan_metadata.json`, the sidecar's `"scan_key"` field is
  `"scan_7"`, and `sleap_roots_predict._load_scan` accepts it (no `error` on the returned
  `ScanInput`)

#### Scenario: scan_key is unique within a stage directory

- **WHEN** `discover_scans` is called on an output directory containing exactly one staged scan
- **THEN** it returns exactly one `ScanInput` (no `ValueError` for duplicate scan_keys)

### Requirement: Sidecar params are resolved from Bloom metadata via the contracts oracle

The command SHALL populate the sidecar's `params` field with the flat dict
`{species, mode, age}` obtained by calling `resolve_params` from `sleap-roots-contracts>=0.1.0a4`
on the scan's `cyl_scans_extended` row with `overrides={"mode": "cylinder"}`, and writing
`resolved.values`. The `mode` value MUST always be `"cylinder"` for the `cyl` command family;
`species` and `age` come from `species_name` and `plant_age_days` in the scan row. The command
SHALL NOT duplicate the normalization logic of `resolve_params`; it MUST import and call the
oracle from `sleap-roots-contracts`.

#### Scenario: Params dict has the required keys with canonical values

- **WHEN** the sidecar is written for a scan with `species_name="Pennycress"`,
  `plant_age_days=14`
- **THEN** the sidecar's `params` dict has keys `species`, `mode`, `age`; `mode` is `"cylinder"`;
  `species` and `age` are the canonicalized values from `resolve_params`; and
  `sleap_roots_predict._load_scan` accepts the params without error

#### Scenario: Params satisfy predict's required-param-keys check

- **WHEN** `discover_scans` loads the staged scan
- **THEN** the returned `ScanInput.params` is a `ResolvedParams` (not None) and the `ScanInput`
  has no `error`

### Requirement: Sidecar image_ids are the real cyl_images ids for the scan

The command SHALL populate the sidecar's `image_ids` field with the list of `cyl_images.id`
integer values for the scan, in DB `frame_number` ascending order. These ids MUST be the real
database ids because the write-back RPC `insert_cyl_result_envelope` resolves
`inputs.image_ids → cyl_images.scan_id`; synthetic or wrong ids cause a "no matching scan"
rejection at write-back time.

#### Scenario: image_ids equal the scan's cyl_images ids

- **WHEN** the sidecar is written for a scan whose `cyl_images` rows have ids `[1001, 1002]` in
  frame_number order
- **THEN** the sidecar's `image_ids` field is `[1001, 1002]` (integers, same order)

Note: whether a `ResultEnvelope` produced downstream from this sidecar actually resolves via the
write-back RPC (`insert_cyl_result_envelope`) is owned and tested by the `cyl-trait-writeback`
capability (see `bloomcli/tests/test_cyl_ingest.py`), not this one — this capability's contract
ends at producing correct, real `cyl_images.id` values in the sidecar.

### Requirement: Sidecar images_checksum ties provenance to the downloaded frames

The command SHALL populate the sidecar's `images_checksum` field with a `"sha256:<hex>"` digest
computed over the frame bytes concatenated in DB `frame_number` ascending order (the same order
as `image_ids`). The checksum MUST be computed over the bytes that were actually written to disk.

#### Scenario: Checksum is sha256-prefixed and computed in frame_number order

- **WHEN** the sidecar is written after downloading frames with frame_numbers 0 and 1
- **THEN** the `images_checksum` field starts with `"sha256:"` and equals the sha256 of the
  frame-0 bytes concatenated with the frame-1 bytes (in that order)

#### Scenario: Checksum changes when frame content changes

- **WHEN** the same scan is staged twice but with different frame bytes for one frame
- **THEN** the two sidecars have different `images_checksum` values

### Requirement: Authentication uses an existing profile

The command SHALL authenticate using an existing `bloomctl` credentials profile (interactive
login), and SHALL surface a clear error when credentials are missing or invalid.

#### Scenario: Missing credentials

- **WHEN** no credentials profile exists or the profile is invalid
- **THEN** the command exits non-zero and prints guidance to run `bloomctl login`
