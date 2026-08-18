## Context

Read in full before this design: `bloomcli/src/bloomctl/cyl/download_for_predict.py` (current
sequential `download_frames_for_predict`, `stage_one_scan`'s lock usage), `bloomcli/src/bloomctl/
cyl/download.py` (`download_frame`/`download_images`, the `cyl download` concurrency PR #623
shipped), `bloomcli/src/bloomctl/plate/download.py` (`download_plate_image`/`download_images`, the
*other* existing concurrent-download command), `bloomcli/src/bloomctl/_download.py` (`fetch_all`,
`run_bounded`, `download_to`, `Fetched`, `DEFAULT_WORKERS`, `MAX_WORKERS` — the shared mechanics),
`bloomcli/src/bloomctl/_storage.py` (`download_object`, `atomic_write_bytes` — the retry/atomic-
write primitives both `download_frame` and `download_to` build on), and `bloomcli/src/bloomctl/
cyl/_locks.py` (the per-scan lock `stage_one_scan` already holds, bloom #655).

## Goals / Non-Goals

**Goals:**
- Parallelize the frame-fetch loop within one scan, using the same shared bounded-concurrency
  pool the two other download commands already use — no new pool implementation.
- Eliminate the duplicate download+atomic-write+retry+error-classification logic the original
  sequential `download_frames_for_predict` re-derived, by building its per-frame worker on the
  already-shared `download_to` primitive instead (mirrors `plate/download.py`'s
  `download_plate_image`).
- Preserve the sidecar checksum's frame-number-order contract under concurrency.
- Compose cleanly with `stage_one_scan`'s existing per-scan lock (bloom #655): the pool must not
  hold that lock any longer than the sequential loop did, and must not itself acquire/release any
  lock (that stays `stage_one_scan`'s job).

**Non-Goals:**
- Any change to the locking or manifest logic (#653/#655's territory).
- Refactoring `cyl download`'s own `download_frame` to also build on `download_to` — see
  "Pre-existing duplication not fixed here" below.
- Multi-scan concurrency (staging several scans of a batch at once). `batch-download-for-predict`
  still stages one scan at a time; only the frame-fetch loop *within* a scan gains concurrency.

## Decisions

### Reuse `download_to`, not a new bespoke per-frame worker

The original (sequential) `download_frames_for_predict` called `download_object` +
`atomic_write_bytes` directly inside its own try/except, hand-rolling the same
"fetch → write atomically → classify failure → never raise" shape that `_download.py`'s
`download_to` already implements and `plate/download.py`'s `download_plate_image` already
delegates to. Rebuilding the new concurrent per-frame worker on `download_to` (resolve `dest` in a
narrow try/except for a malformed row, then delegate the actual fetch/write/retry/disk-full logic
to `download_to`, then map its `Fetched` onto this command's own `FrameResult`) eliminates that
duplication rather than compounding it with a second hand-rolled copy inside a new concurrent
loop. This is the same shape `download_plate_image` already uses — not a new pattern introduced
by this change.

### Bytes for the checksum come from a post-write read-back, not a widened `download_to` contract

`download_to` returns `Fetched(ok, skipped, error, note)` — never the downloaded bytes — because
neither `cyl download` nor `plate download` need them after the file is on disk. This command's
sidecar checksum does need them (`compute_checksum` hashes frame bytes in DB `frame_number`
order). Two options were considered:

1. **Read the file back off disk** (`dest.read_bytes()`) after a successful `download_to` call.
2. **Widen `Fetched`/`download_to`** with an optional `capture_bytes` flag / a `data: bytes | None`
   field, so the caller can get the bytes back without a second I/O.

Option 1 was chosen. `download_to` is a small, already-tested, two-caller-shared primitive;
widening its return contract for the benefit of a third caller that is the only one needing bytes
back adds a field/parameter every other caller and every existing test of `download_to` has to
account for, for no benefit to them (YAGNI: don't generalize a shared primitive for a need only
one caller has). The extra local file read costs one additional disk read per frame — image
files, not large relative to the network fetch that already dominates each frame's wall-clock
cost — and keeps `download_to`'s contract, and its existing test coverage in `cyl download` and
`plate download`, completely unchanged. It also happens to satisfy the existing
`cyl-download-for-predict` spec's "computed over the bytes that were actually written to disk"
wording more literally than the prior sequential implementation did (which hashed the in-memory
buffer immediately after the network response, before `atomic_write_bytes`'s `os.replace` was
known to have landed).

### `fetch_all`, not a new `ThreadPoolExecutor`

`_download.py`'s `fetch_all`/`run_bounded` already implement the bounded-window concurrent-worker
pattern both `cyl download` and `plate download` use (`workers <= 1` runs sequentially with no
pool at all; otherwise a `ThreadPoolExecutor` capped at `min(workers, MAX_WORKERS, len(work))`).
`download_frames_for_predict` calls `fetch_all` directly rather than re-implementing pool sizing,
windowing, or the `workers <= 1` sequential fast path — the issue's own suggested approach
("mirror #623's pattern") is satisfied by reusing the exact primitive #623 introduced, not by
duplicating its shape.

`fetch_all`/`run_bounded` store each item's outcome by its position in the input list, filled in
as futures complete — not by completion order. Passing `images` directly (in DB `frame_number`
order) as the work list means the returned outcomes list is already in that same order regardless
of which thread finishes first, so `download_frames_for_predict` can build `frame_bytes` by a
plain positional list comprehension over the outcomes, with no separate re-sorting step.

### Disk-full stop is a consequence of reuse, not new scope

`download_to` already accepts an optional `stop: threading.Event`, and both `cyl download` and
`plate download`'s orchestrators create one `threading.Event()` per invocation and pass it to
every worker so a full disk stops further in-flight-but-not-yet-started frames from being fetched
only to be discarded. `download_frames_for_predict` does the same: one `stop` Event per call,
threaded into every `download_to` call in the pool. This was raised as an open question (add
disk-full handling as new scope, or leave it out per the issue's narrower literal ask) before this
reuse design was settled; once the per-frame worker is built on `download_to` anyway, accepting
disk-full protection is a one-line wiring decision, not new logic written for this change — so
there is no longer a real cost/scope trade-off to defer. Each `stage_one_scan` call constructs its
own `download_frames_for_predict` call and therefore its own `stop` Event — a disk-full condition
encountered while staging one scan in a batch does not carry over into the next scan's pool, since
nothing shares state across scans.

### Lock composition: unchanged from bloom #655, verified not just assumed

`stage_one_scan` already holds `out_dir/.locks/{scan_key}.lock` from the skip-check through the
sidecar write (bloom #655, shipped separately, not modified here). The concurrent frame-fetch pool
runs entirely inside that `with acquire_lock(...)` block, exactly where the sequential loop ran
before; `fetch_all` blocks until every submitted frame's future completes before
`download_frames_for_predict` returns, so the lock's critical section is not shortened, extended,
or exited early by the pool's internal concurrency — the invocation still holds the lock for one
contiguous critical section, just for less wall-clock time inside it. Nothing in this change
acquires or releases a lock itself; the worker threads are unaware the lock exists.

### `workers=1` still runs one at a time, on the caller's own thread

`fetch_all`'s `workers <= 1` path runs every item sequentially on the calling thread with no
`ThreadPoolExecutor` constructed — matching `cyl download`'s and `plate download`'s existing
`--workers 1` behavior. `download_frames_for_predict(..., workers=1)` is therefore
behavior-equivalent to the pre-change sequential loop for the ordinary success/failure paths (same
order, same per-frame isolation, one thread), not merely a pool-of-size-1.

**Carve-out:** this equivalence does not extend to disk-full behavior. Section 4 (disk-full `stop`
wiring, below) creates one `stop` Event per `download_frames_for_predict` call unconditionally —
regardless of `workers` — because that Event is threaded into `download_to` the same way at any
worker count. `download-for-predict` had no disk-full handling at all before this change (the
sequential loop just let each frame's write raise, one at a time, with no fail-fast); after this
change, even a `--workers 1` run stops attempting further frames once one hits a disk-full
condition. This is a deliberate, real, observable behavior change to the existing sequential
command — noted explicitly here, and reflected in both delta specs' disk-full scenario ("at any
`--workers` value, including `--workers 1`"), not an incidental side effect glossed over as "no
change at `--workers 1`."

### Pre-existing duplication not fixed here: `cyl download`'s own `download_frame`

`cyl/download.py`'s `download_frame` predates `download_to` (or was never migrated to it) and
still hand-rolls its own fetch+write+retry+error-classification logic, parallel to what
`download_to` already provides — the same duplication this change avoids introducing a *second*
copy of. Refactoring `download_frame` to build on `download_to` is real cleanup, but it is
out of scope here: bloom #652 doesn't ask for it, and `cyl download` is an already-shipped,
heavily test-hardened command (resume, token-refresh, collision detection, disk-full handling) —
touching its frame loop for an unrelated ticket is its own regression surface. Flagged here as a
follow-up, not fixed in this change.

## Risks / Trade-offs

- One extra local disk read per successfully-downloaded frame (the read-back for checksum bytes).
  Negligible relative to the network fetch each frame already performs; accepted in exchange for
  leaving `download_to`'s shared, two-caller-tested contract untouched.
- `download_to`'s `expected_size` parameter is not used here (passed as `None`, its default) —
  `download-for-predict` always downloads into a directory `clear_scan_dir` just emptied, so
  `already_downloaded`'s resume-skip check inside `download_to` can never find a pre-existing file
  to compare against at frame-fetch time; this is a harmless no-op, not a partially-adopted
  feature, since resume for this command already happens one level up (`scan_is_already_staged`'s
  whole-scan skip check).
