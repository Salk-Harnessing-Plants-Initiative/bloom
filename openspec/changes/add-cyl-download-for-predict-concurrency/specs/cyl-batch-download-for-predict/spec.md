## ADDED Requirements

### Requirement: Each scan's frame downloads run through a bounded concurrent worker pool

The `cyl batch-download-for-predict` command SHALL accept a `-n`/`--workers` option (integer,
1-64 inclusive, default 8) controlling how many of each scan's frames download concurrently,
applied independently to every scan in the batch. Scans themselves SHALL continue to be staged one
at a time (this option does not introduce cross-scan concurrency). The per-scan lock
(`out_dir/.locks/{scan_key}.lock`) SHALL be held for the entire duration of that scan's concurrent
frame-fetch pool, exactly as it is held for the sequential case — the pool SHALL be fully complete
before the lock is released, so two invocations racing on the same `scan_id` remain protected the
same way regardless of `--workers`.

#### Scenario: --workers applies per scan across a batch

- **WHEN** the user runs `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids 1,2
  --workers 4` and both scans have several frames each
- **THEN** each scan's frames download using up to 4 concurrent workers, and both scans are staged
  correctly (frames + sidecar present, matching what `--workers 1` would produce)

#### Scenario: --workers is bounded to 1-64

- **WHEN** the user passes `--workers 0`, `--workers 65`, or a negative value
- **THEN** the command exits non-zero with a usage error before any scan is staged

#### Scenario: The per-scan lock is held for the whole concurrent frame-fetch pool

- **GIVEN** a scan's frame downloads are running concurrently under `--workers 4`
- **WHEN** any one of those concurrent frame downloads is in flight
- **THEN** that scan's per-scan lock file (`out_dir/.locks/{scan_key}.lock`) exists, and it no
  longer exists once the scan finishes staging — the lock's critical section is not shortened,
  lengthened, or exited early by the pool's internal concurrency

#### Scenario: Default worker count matches the single-scan command

- **WHEN** the user omits `--workers`
- **THEN** each scan's frames download with the same default worker count (8) as
  `cyl download-for-predict`'s own default

#### Scenario: A disk-full condition within one scan is isolated to that scan

- **WHEN** writing a frame's bytes to disk fails because the disk is full or the storage quota is
  spent while staging one scan in the batch, at any `--workers` value, with other scans still
  pending
- **THEN** that scan is reported `failed` the same way any other partial frame-download failure is
  reported, and the remaining scans in the batch are still staged normally (a disk-full condition
  in one scan's pool does not abort the batch, and does not affect any other scan's independent
  disk-full tracking)
