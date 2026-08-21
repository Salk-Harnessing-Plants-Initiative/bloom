## ADDED Requirements

### Requirement: Frame downloads run through a bounded concurrent worker pool

The `cyl download-for-predict` command SHALL accept a `-n`/`--workers` option (integer,
1-64 inclusive, default 8) controlling how many of a scan's frames download concurrently.
`--workers 1` SHALL download frames one at a time, on the calling thread, with no thread pool
constructed. A value outside 1-64 SHALL be rejected before any I/O occurs. Regardless of the
worker count, the sidecar's `image_ids` and `images_checksum` fields SHALL be computed in the same
DB `frame_number` ascending order as the sequential (single-worker) case, independent of which
worker thread completes first, and one frame's download failure SHALL NOT abort or corrupt the
outcome of any other frame's concurrent download.

#### Scenario: Frames for one scan download concurrently

- **WHEN** the user runs `bloomctl cyl download-for-predict 7 /tmp/stage --workers 4` for a scan
  with 8 or more frames
- **THEN** at least 4 frames are observably in flight to Storage at the same time (not fetched one
  at a time), and all frames land on disk correctly

#### Scenario: --workers 1 runs sequentially with no pool

- **WHEN** the user runs the command with `--workers 1`
- **THEN** frames download one at a time on the calling thread and no thread pool is constructed

#### Scenario: --workers is bounded to 1-64

- **WHEN** the user passes `--workers 0`, `--workers 65`, or a negative value
- **THEN** the command exits non-zero with a usage error before any download or directory-clearing
  action occurs

#### Scenario: Checksum frame order is preserved regardless of completion order

- **WHEN** a scan's frames download concurrently and a later-`frame_number` frame's network fetch
  completes before an earlier-`frame_number` frame's
- **THEN** the sidecar's `images_checksum` is identical to what a sequential (`--workers 1`)
  download of the same frame bytes would have produced — computed over frame bytes in ascending
  `frame_number` order, not completion order

#### Scenario: One bad frame among several concurrent downloads is isolated

- **WHEN** one frame's download fails (a storage error) while `--workers` is greater than 1
- **THEN** every other frame in the scan still downloads successfully, and the command reports the
  one failure the same way it would under `--workers 1` (frames downloaded this run remain on
  disk, no sidecar is written)

#### Scenario: A disk-full condition stops further frames without discarding in-flight work

- **WHEN** writing a frame's bytes to disk fails because the disk is full or the storage quota is
  spent, at any `--workers` value (including `--workers 1`, where no such handling existed before
  this capability)
- **THEN** frames whose download had not yet started are recorded as failed without being
  fetched, any frame whose download was already under way completes normally rather than being
  interrupted, and the command reports the outcome the same way it reports any other partial
  frame-download failure (non-zero exit, no sidecar written, frames already on disk remain)
