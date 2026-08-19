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

## Post-PR-review hardening (found via `/review-pr` on #698, fixed same PR)

A 5-lens adversarial review after the initial implementation landed found two real bugs, both
now fixed in the same PR, plus pre-existing gaps this change does not touch.

- **`_download_one_frame_for_predict`'s post-write `dest.read_bytes()` was unguarded, contradicting
  its own "never raises" docstring.** `download_to` is careful to catch every filesystem/network
  failure internally, but the read-back step added by this change (see "bytes for the checksum"
  above) had no equivalent guard — an `OSError` on the read (a disk fault, a permissions change, an
  AV lock on Windows) would propagate out of a worker thread, through `fetch_all`/`run_bounded`'s
  `future.result()`, and — for the single-scan `download-for-predict` command specifically, which
  has no enclosing `try/except` around this call the way `stage_one_scan` does — surface as a raw
  traceback instead of a clean `click.ClickException`. The pre-fix blast radius was worse than "one
  frame fails": `run_bounded`'s `wait(pending, return_when=FIRST_COMPLETED)` can return several done
  futures per iteration, and calling `.result()` on each in a loop means a raised exception on one
  aborts the loop with *other, already-completed* futures in that same batch never collected —
  their bytes are correctly on disk, but their outcomes are silently discarded, not just the
  triggering frame's. Fixed by wrapping the read in its own `try/except OSError`, converting a
  read-back failure into an ordinary failed `FrameResult` (no bytes), the same "never raise, record
  and continue" discipline every other failure mode in this function already follows — this also
  means `run_bounded`'s loop never sees a raised exception here in the first place, closing the
  discarded-sibling-results issue too, not just the crash. Covered by
  `test_post_write_read_failure_is_a_failed_result_not_a_raised_exception` (direct call; confirmed
  RED against the pre-fix code — the exception propagated exactly as predicted — before the fix
  landed) and, added in a second review round,
  `test_a_read_back_failure_in_the_pool_does_not_affect_sibling_frames` (`workers=4`, proving
  isolation through the real pool, not just the per-frame helper in isolation).
- **`DownloadResult.disk_full` was never set on the value `download_frames_for_predict` returns,
  unlike `cyl download`'s and `plate download`'s identical orchestrators (both do
  `DownloadResult(frames, disk_full=stop.is_set())`).** Consequence: even after this change added
  real disk-full fail-fast behavior (see "Disk-full stop is a consequence of reuse" above), neither
  calling command's failure message ever said so — an operator hitting a full disk saw the same
  generic "N of M frames failed to download" wording as any other transient failure, with none of
  `cyl download`'s/`plate download`'s "the disk filled up or the storage quota was spent" framing.
  Fixed two ways: (1) `download_frames_for_predict` now returns
  `DownloadResult(frames, disk_full=stop.is_set())`; (2) both call sites
  (`download_for_predict`'s `click.ClickException` and `stage_one_scan`'s `ScanResult` error text)
  now prepend the same cause string `cyl download`/`plate download` already use, conditioned on
  `result.disk_full`. Covered by
  `test_disk_full_is_surfaced_on_the_result_and_in_both_commands_error_messages` (confirmed RED
  against the pre-fix code on both the field and the message text before the fix landed;
  exercises `stage_one_scan`'s message directly) and, added in a second review round,
  `test_cli_disk_full_error_message_names_the_cause` (the other call site — `download_for_predict`'s
  own `click.ClickException` — exercised end to end through the real CLI via `CliRunner`, not just
  `stage_one_scan`'s).
- **Diagnostic value of the read-back-failure message is currently limited (found in a second
  review round, not fixed here — pre-existing, not introduced by this PR).** Neither
  `download_for_predict` nor `stage_one_scan` surfaces individual `FrameResult.error` text anywhere
  in their output — both only report the aggregate `"{cause}{failed} of {total} frames failed to
  download"`. Unlike `cyl download`, which writes a per-frame `download_log.txt` via
  `write_download_log`, this command has no equivalent. So the specific message this fix adds
  (`"downloaded but could not read it back for the checksum: {exc}"`) is computed but never shown to
  an operator — indistinguishable in every user-visible surface from an ordinary storage-download
  failure. This predates this PR (the command never had per-frame logging) and isn't a regression,
  but it undercuts this fix's own diagnostic intent. Worth a follow-up: either a
  `write_download_log`-style per-frame log for this command, or per-frame errors surfaced in
  `--json` batch output.

Findings surfaced but deliberately not fixed in this PR, since they predate it and are not
introduced or worsened by it (flagged here so a future change has the context, not silently
dropped):

- `frame_dest_for_predict` has no equivalent of `image_dest`'s explicit path-containment guard
  (`os.path.normpath` + `isabs`/`pardir` check via the shared `contained_dest` helper). Not
  currently exploitable — `cyl_images.frame_number` is a Postgres `INT` column (PostgREST always
  serializes it as a JSON integer) and `Path(...).suffix` can never contain a separator — but the
  safety rests entirely on that DB-schema invariant rather than a runtime check in the code that
  uses it, unlike the sibling command. Worth hardening as a follow-up.
- `download_for_predict` (the single-scan command) has no lock at all — only `stage_one_scan`
  (the batch path) acquires one. Predates this change and is unchanged by it — worth being precise
  about the severity, corrected in a second review round: two racing sequential (pre-PR,
  `workers=1`-equivalent) invocations could already interleave one's `clear_scan_dir` `rmtree` with
  the other's in-progress writes, an already-unbounded/non-atomic corruption risk, not a mild one.
  Frame-level concurrency changes the *interleaving pattern* (more simultaneous filesystem
  operations per invocation) while also *shortening* each invocation's total wall-clock exposure
  window — these partially offset rather than straightforwardly compounding. The honest framing is
  "the failure pattern changes, the severity ceiling does not," not "raises the blast radius."
  Incidentally, this PR's `dest.read_bytes()` fix above also means the specific TOCTOU where a
  racing invocation's `clear_scan_dir` deletes a just-written frame between its write and read-back
  now degrades to a clean failed `FrameResult` (a `FileNotFoundError`/`OSError` the new
  `try/except` catches) rather than a crash — a side benefit, not a fix for the underlying
  missing-lock race, which still ends the run in a failure either way.
- A `SIGINT`/`KeyboardInterrupt` mid-batch still skips the `RunManifest` write for every scan
  already staged in that invocation (`stage_one_scan`'s `except Exception` doesn't catch
  `BaseException`). Predates this change; unaffected by it.
