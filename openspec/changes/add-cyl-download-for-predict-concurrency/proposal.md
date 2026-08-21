## Why

`bloomctl cyl download-for-predict` / `batch-download-for-predict` (A4's `images-downloader`
stage-in) download a scan's frames sequentially, one at a time. PR #623 (merged 2026-08-07) added
an 8-worker concurrent pool to the sibling `cyl download` command, explicitly not touching this
one — its own body says `download-for-predict` "stages one scan at a time, so it isn't where the
time goes." True for a single scan, but `batch-download-for-predict` stages N scans per Argo
`images-downloader` run, so the sequential-frames-within-a-scan cost adds up across the whole
batch and affects end-to-end pipeline latency (bloom #652).

## What Changes

- Add a `-n`/`--workers` option (1-64, default 8 — the same range and default `cyl download`
  already uses) to both `download-for-predict` and `batch-download-for-predict`. `--workers 1`
  runs sequentially with no thread pool spawned at all.
- Frame downloads for one scan run through the **existing shared bounded-concurrency pool**
  (`fetch_all` in `bloomctl/_download.py`) — the same mechanism `cyl download` and `plate
  download` already use — rather than a new bespoke `ThreadPoolExecutor`.
- The per-frame worker is rebuilt on the **existing shared `download_to` primitive**
  (`bloomctl/_download.py`), mirroring `plate/download.py`'s `download_plate_image`, instead of
  re-deriving the download+atomic-write+retry+error-classification logic `download_to` already
  provides. `download_to` already accepts an optional `stop: threading.Event` for
  disk-full handling; wiring one through (as `cyl download`'s and `plate download`'s own
  orchestrators already do) gets that protection as a side effect of reuse, not new bespoke
  logic written for this change. **This is a real, observable behavior change worth calling out
  explicitly**: `download-for-predict` had no disk-full handling at all before this change, at any
  `--workers` value — the `stop` Event is created unconditionally per call, so even `--workers 1`
  (previously fully sequential with no fail-fast) now stops attempting further frames once one
  hits a disk-full condition, rather than letting each remaining frame independently fail. Not
  merely a side effect of concurrency; see design.md's "workers=1" decision.
- `download_to` returns `Fetched(ok, skipped, error, note)` — no bytes — since neither of its two
  existing callers need the downloaded bytes back, but this command's sidecar checksum does. The
  per-frame worker reads the file back off disk (`dest.read_bytes()`) after a successful
  `download_to` call to get those bytes, rather than widening `download_to`'s shared contract for
  one new caller's need (see design.md's "bytes for the checksum" decision for the two options
  considered and why).
- Frame outcomes are assembled in `images`' original (DB `frame_number`) order regardless of
  which worker thread finishes first — `fetch_all` returns outcomes positionally, so the sidecar
  checksum's frame-number-order contract holds under concurrency exactly as it holds today under
  the sequential loop.
- `stage_one_scan`'s existing per-scan lock (bloom #655, already shipped) wraps the now-concurrent
  frame-fetch loop unchanged: the worker pool is fully joined (via `fetch_all`) before
  `stage_one_scan` returns from inside the `with acquire_lock(...)` block, so the lock is held for
  the same critical section as before, just less wall-clock time.

**Explicitly out of scope** (do not touch in this change):

- The locking/manifest logic itself (#653/#655's territory) — untouched, only composed with.
- write-back's unscoped-glob bug (bloom #678) — separate issue, separate change.
- Refactoring `cyl download`'s own `download_frame` (in `cyl/download.py`) to also build on
  `download_to` instead of its own hand-rolled fetch+write logic. This is real, pre-existing
  duplication between `download_frame` and `download_to`/`download_plate_image`, but it predates
  this change, isn't something bloom #652 asks for, and touching an already-shipped, heavily
  test-hardened command's frame loop for an unrelated ticket carries its own regression risk —
  noted as a follow-up in design.md, not fixed here.

## Impact

- **Affected code**: `bloomcli/src/bloomctl/cyl/download_for_predict.py` (per-frame worker
  rebuilt on `download_to`; `download_frames_for_predict` gains `workers`; both commands gain
  `-n`/`--workers`; `stage_one_scan` threads `workers` through); new
  `bloomcli/tests/test_download_for_predict_concurrency.py`; `bloomcli/CHANGELOG.md`;
  `bloomcli/README.md` (both commands' synopsis lines and option lists document the new
  `-n`/`--workers` flag, matching the existing `--lock-staleness-seconds` bullet style).
- **Capability extended**: `cyl-download-for-predict` and `cyl-batch-download-for-predict` each
  gain an `ADDED` requirement describing concurrent frame downloads; no existing requirement's
  behavior changes (nothing in either spec currently asserts sequential execution).
- **No server/RPC/schema changes. No dependency version changes.**
- **Cross-repo follow-up (not part of this change's own tasks)**: once merged, update
  `sleap-roots-pipeline`'s `docs/bloom-integration/roadmap.md` (`images-downloader` row, A4 change
  breakdown table) noting the concurrency improvement with this change's PR link, plus a
  status-log entry.
- **Tracking**: bloom #652. Builds on bloom #655 (`_locks.py`, already shipped) by composing with
  it, without modifying it.
