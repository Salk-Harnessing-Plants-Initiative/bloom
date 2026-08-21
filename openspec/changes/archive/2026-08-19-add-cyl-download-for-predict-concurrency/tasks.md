TDD throughout: write the failing test first, confirm RED (run it against the current code and
see it fail for the reason the task states, not an unrelated error), then implement to GREEN. This
is a staging-first, protected repo — every pushed commit must keep CI green, so new tests and the
code they exercise land in the same commit. Only `bloomcli/src/bloomctl/cyl/download_for_predict.py`
is modified; `_download.py`, `_storage.py`, `_locks.py`, and `cyl/download.py` are read but not
changed (see design.md's "Pre-existing duplication not fixed here").

Some tasks (across sections 1, 2, 4, and 5) are labeled **VERIFY** rather than RED/GREEN: they
exercise already-shipped, already-tested shared machinery (`fetch_all`'s pool sizing/ordering and
`download_to`'s stop-before-start/no-mid-flight-interrupt semantics, both from PR #623) through
this command's new call sites, or a property (e.g. per-call `stop` Event isolation) that already
follows from a preceding GREEN task by construction. A VERIFY task's test is written and run, but
it is not expected to fail before any further code is written — if it does fail, that is itself a
finding, not an expected step. Only label a task RED if its test genuinely cannot pass against the
code that exists at the point it is written and run — if a "RED" task's own description has to
explain why it wouldn't actually fail as sequenced, relabel it VERIFY instead of leaving the
mismatch in prose.

Every `threading.Barrier(...)` used in a test below MUST specify `timeout=5` (matching
`test_download_concurrency.py`'s existing pattern) so a real deadlock regression fails fast
instead of hanging CI indefinitely — never construct an unbounded barrier. Any test asserting
"not yet started" vs "already in flight" behavior MUST use an explicit synchronization primitive
(a barrier, or a sequential/`workers=1` setup where ordering is deterministic by construction) —
never an unsynchronized frame count or sleep-based race, which can pass or fail depending on
thread scheduling.

**Section dependencies**: Section 1 (per-frame worker on `download_to`) has no dependency on the
others — it only touches the module-private per-frame function. Section 2 (`fetch_all`
orchestration) depends on section 1 (needs the new per-frame worker to call). Section 3 (`--workers`
CLI) depends on section 2 (`download_frames_for_predict` must accept `workers` before a CLI option
can thread it through). Section 4 (disk-full `stop` wiring) depends on section 2. Section 5 (lock
composition) is a verification-only section and can run any time after section 3, since it exercises
the full `stage_one_scan` path. Section 6 (docs) is last. Land order: 1 → 2 → 3 → 4 → 5 → 6.

## 1. Per-frame worker rebuilt on `download_to`

- [x] 1.1 RED: a test calling the (not-yet-existing) per-frame worker directly with a malformed
      row (missing `object_path` or `frame_number`) asserts it returns a `failed` `FrameResult`
      rather than raising. Run against current code: fails because no such function exists yet
      (import error / `AttributeError`).
- [x] 1.2 GREEN: add a module-private per-frame worker to `download_for_predict.py` that resolves
      `dest` via `frame_dest_for_predict` inside a narrow `try/except (KeyError, TypeError)`
      (malformed row → failed `FrameResult`, mirroring `download_plate_image`'s own dest-resolution
      guard) and returns early — no `download_to` call yet. Confirm 1.1 passes and no more.
- [x] 1.3 RED: a test with a well-formed row and a fake bucket asserts the worker's `FrameResult`
      is `ok`, the frame's bytes land at the correct destination path, and the worker returns
      those bytes as a second value in a `(FrameResult, bytes)` tuple. Fails against 1.2's
      implementation (no `download_to` call, no bytes returned).
- [x] 1.4 GREEN: have the worker delegate to `download_to(client, object_path, dest,
      bucket=IMAGES_BUCKET, stop=None)` after the dest-resolution guard, map the returned
      `Fetched` onto the `FrameResult` (both the success and failure branches — `Fetched.ok is
      False` maps to a `failed` `FrameResult` with no bytes returned), and on success return
      `dest.read_bytes()` as the second tuple element. Confirm 1.3 passes.
- [x] 1.5 VERIFY: a test that makes the fake bucket's `download` return one payload while a
      monkeypatched `atomic_write_bytes` (reached through `download_to`) writes a different,
      distinguishable payload to disk — asserts the worker's returned bytes equal what is actually
      on disk afterward, not what the fake bucket returned. This is the test that actually proves
      the "bytes come from a post-write read-back, not the in-memory download response" design
      decision, and is not expected to require any code beyond 1.4's `dest.read_bytes()` — but it
      is the only test that would catch a regression to returning the in-memory `download_object`
      result instead, so it belongs here even though it can't be RED given 1.4's own wording.
- [x] 1.6 VERIFY: a test with a bucket whose `download` raises (a storage error) asserts the worker
      returns a `failed` `FrameResult` with the second tuple element `None` (no bytes), and that no
      partial or temp file is left on disk (`atomic_write_bytes`'s own guarantee, exercised
      transitively through `download_to`). Exercises the failure branch of 1.4's `Fetched` mapping;
      not expected to require additional code.

## 2. Bounded concurrent pool via `fetch_all`

- [x] 2.1 RED: `download_frames_for_predict` gains a `workers: int = DEFAULT_WORKERS` keyword-only
      parameter (import `DEFAULT_WORKERS`, `MAX_WORKERS`, `fetch_all` from `.._download`). A test
      calling it with `workers=4` and 8 images against a bucket whose `download` blocks on
      `threading.Barrier(4, timeout=5)` — passes only if at least 4 frames are genuinely in flight
      at once. Fails against the current sequential loop (deadlocks and hits the barrier's
      5-second timeout, failing the test rather than hanging CI indefinitely).
- [x] 2.2 GREEN: replace the sequential `for image in images` loop with a single
      `fetch_all(images, <worker from section 1>, workers=workers)` call; build `frames` and
      `frame_bytes` from the returned (positionally-ordered) outcomes. Confirm 2.1 passes.
- [x] 2.3 VERIFY: order-preservation test — a bucket whose `download` sleeps longer for *earlier*
      `frame_number`s (so later frames finish first), asserting `frame_bytes` still comes back in
      ascending `frame_number` order and `compute_checksum(frame_bytes)` matches the checksum
      computed from the same bytes in submission order. Exercises `fetch_all`'s existing
      positional-result guarantee (from PR #623) through this new call site; not expected to
      require code beyond 2.2.
- [x] 2.4 VERIFY: one-bad-frame-among-several test under concurrency (`workers=4`, 6 images, one
      `object_path` always raising) — asserts `result.ok == 5`, `result.failed == 1`, and
      `len(frame_bytes) == 5` (the failed frame contributes no bytes entry). Not expected to
      require code beyond section 1's per-frame isolation.
- [x] 2.5 VERIFY: a malformed row (missing `frame_number`) mixed into an otherwise well-formed
      `images` list with `workers=4` — asserts the malformed row's failure survives
      `fetch_all`/`run_bounded`'s `future.result()` call without raising and aborting the other
      frames in the pool (distinct from 1.1, which tests the worker function directly, not
      routed through the pool).
- [x] 2.6 VERIFY: `workers=1` test with a spied `ThreadPoolExecutor` (monkeypatched on
      `bloomctl._download`) asserting no pool is constructed at all, and that results still come
      back correctly ordered — matches `fetch_all`'s existing `workers <= 1` sequential path.
- [x] 2.7 VERIFY: pool-ceiling tests mirroring `test_download_concurrency.py`'s
      `test_the_pool_never_exceeds_the_ceiling_even_via_a_direct_call` and
      `test_the_pool_is_never_larger_than_the_work` — `workers=1000` against 200 images asserts
      the constructed pool's `max_workers == MAX_WORKERS`; `workers=32` against 3 images asserts
      `max_workers == 3`. Exercises `fetch_all`'s existing sizing logic, not new code.

## 3. `-n`/`--workers` CLI option (both commands)

- [x] 3.1 RED: `download_for_predict` (single-scan command) test spying on
      `download_frames_for_predict` to assert `--workers 3` reaches it as `workers=3`. Fails
      against current code: the CLI has no `--workers`/`-n` option, so Click rejects the flag with
      a usage error before the spy is ever called.
- [x] 3.2 GREEN: add `-n`/`--workers` to `download_for_predict` —
      `type=click.IntRange(min=1, max=MAX_WORKERS)`, `default=DEFAULT_WORKERS`,
      `show_default=True`, and `help=f"Concurrent frame downloads (I/O-bound, 1-{MAX_WORKERS}). 1
      = sequential."` (matching `cyl download`'s/`plate download`'s own `--workers` help string
      verbatim in structure — every existing option on this command has a `help=` string; house
      style, not optional) — and thread the option value into its `download_frames_for_predict`
      call. Confirm 3.1 passes.
- [x] 3.3 RED: default-value test (no `--workers` flag) asserts `workers == DEFAULT_WORKERS`
      reaches `download_frames_for_predict`. Fails against 3.1's state (no option exists yet) the
      same way 3.1 does; write and confirm alongside 3.1 before implementing 3.2.
- [x] 3.4 GREEN: confirmed by 3.2's `default=DEFAULT_WORKERS`.
- [x] 3.5 RED: boundary tests — `--workers 0`, `--workers 65`, `--workers -2` each assert a
      non-zero exit before any I/O; `--workers 1` and `--workers 64` (the inclusive bounds) each
      assert a zero exit and normal staging. Write and confirm alongside 3.1/3.3, before 3.2 exists:
      with no `--workers` option at all, every case here is currently rejected by Click as an
      unknown option — including the two (`1`, `64`) that must eventually succeed — so this is
      genuinely RED at that point, not just after 3.2 partially lands.
- [x] 3.6 GREEN: confirmed by 3.2's `click.IntRange(min=1, max=MAX_WORKERS)`.
- [x] 3.7 RED: `stage_one_scan` test asserting it accepts a `workers` keyword argument and passes
      it through to `download_frames_for_predict`. Fails against current code: `stage_one_scan`
      raises `TypeError` on an unexpected keyword argument.
- [x] 3.8 GREEN: add `workers: int = DEFAULT_WORKERS` to `stage_one_scan`'s signature; pass it to
      its `download_frames_for_predict` call. Confirm 3.7 passes.
- [x] 3.9 RED: two tests for `batch_download_for_predict`: (a) spying on `stage_one_scan` to
      assert `--workers 5` reaches every call as `workers=5`; (b) an end-to-end barrier test —
      `--scan-ids 1,2 --workers 4` where both scans have 8 frames each and the fake bucket's
      `download` blocks on `threading.Barrier(4, timeout=5)` — asserting both scans stage
      successfully without the barrier timing out. (b) is the batch command's analog of 2.1;
      without it, concurrency is only proven at the `download_frames_for_predict` level, never
      through the full CLI. Both fail against current code: no `--workers` option exists.
- [x] 3.10 GREEN: add the same `-n`/`--workers` option (same `IntRange`, default, and a
      `help=f"Concurrent frame downloads per scan (I/O-bound, 1-{MAX_WORKERS}). 1 = sequential."`
      string) to `batch_download_for_predict`; thread it into the per-scan
      `stage_one_scan(...)` calls. Confirm both parts of 3.9 pass.
- [x] 3.11 RED: default-value and boundary tests for the batch command's `--workers`, mirroring
      3.3/3.5 (including the `--workers 1` / `--workers 64` accepted-boundary cases). Write and
      confirm alongside 3.9, before 3.10 exists, for the same reason 3.5 is written alongside
      3.1/3.3.
- [x] 3.12 GREEN: confirmed by 3.10's option definition (same `IntRange`/default as the
      single-scan command).

## 4. Disk-full `stop` wiring

- [x] 4.1 RED: a deterministic, sequential (`workers=1`) test with 3 frames: the first downloads
      normally, the second's write raises an out-of-space `OSError` (matching `_download.py`'s
      `OUT_OF_SPACE` errno set), and the third's `download` is asserted **never called** — the
      third frame is recorded failed with `download_to`'s existing "nowhere left to write —
      nothing further was downloaded" message instead of being fetched and discarded. `workers=1`
      is used deliberately here (not a barrier/concurrency setup) so "not yet started" is
      deterministic by construction — frame 3 genuinely cannot have started before frame 2's
      failure is observed on one thread — rather than depending on thread-scheduling timing. Fails
      against section 2's implementation: no `stop` Event exists, so the third frame's `download`
      is unconditionally attempted regardless of the second frame's failure.
- [x] 4.2 GREEN: `download_frames_for_predict` creates one `stop = threading.Event()` per call,
      unconditionally (regardless of `workers`), and passes it to every `download_to` invocation
      via the section-1 worker (which gains a `stop` parameter, forwarded to its own `download_to`
      call), matching `cyl download`'s and `plate download`'s own orchestrators. Confirm 4.1
      passes. Note: this makes disk-full fail-fast a real, observable behavior change to the
      *sequential* (`--workers 1`) path too, not only to concurrent runs — `download-for-predict`
      had no disk-full handling at all before this section; see design.md's "workers=1" decision,
      updated to reflect this.
- [x] 4.3 VERIFY: a concurrency test using `workers=2` and a `threading.Barrier(2, timeout=5)`
      inside the fake bucket's `download` to guarantee two frames' downloads are genuinely
      in-flight together before either's write completes; one of the two frames' writes then
      raises the out-of-space `OSError` (setting `stop`) while the other's write succeeds —
      asserts the second (already in-flight) frame still completes and is recorded `ok`, not
      aborted mid-flight, even though `stop` became set while it was in flight. This exercises
      `download_to`'s existing "check `stop` only before starting, never interrupt an in-flight
      call" contract (already true of `download_to` itself, per PR #623/plate download precedent)
      through the new pool; not expected to require code beyond 4.2.
- [x] 4.4 VERIFY: a batch-level test — one scan's frame pool hits the simulated disk-full condition
      from 4.1 while a second scan in the same batch has not yet started — asserts the first scan
      is reported `failed` (per the new `cyl-batch-download-for-predict` spec scenario) and the
      second scan still stages successfully. Exercises the fact that each `stage_one_scan` call
      constructs its own independent `stop` Event (a natural consequence of 4.2's per-call local
      variable, not shared state) — not expected to require additional code.

## 5. Lock composition (verification, no new production code expected)

- [x] 5.1 VERIFY: a test staging a scan with `workers=4` and a `threading.Barrier(4, timeout=5)` in
      the fake bucket's `download`, asserting the per-scan lock file
      (`out_dir/.locks/{scan_key}.lock`) exists at every point a frame download is in flight, and
      no longer exists once `stage_one_scan` returns. If this fails, it means the concurrent pool
      is somehow escaping the `with acquire_lock(...)` block from bloom #655 — expected to pass
      with no additional implementation, given section 2's `fetch_all` call already fully joins
      before `download_frames_for_predict` returns.
- [x] 5.2 VERIFY: a test that a disk-full-induced scan failure (section 4's 4.1 setup, or a batch
      wrapping it) still releases the per-scan lock — the lock file no longer exists once
      `stage_one_scan` returns `failed`. `download_to` swallows the `OSError` internally and never
      raises out of the `with acquire_lock(...)` block, so this is expected to already hold, the
      same way it holds for every other per-scan failure mode `stage_one_scan` isolates — but it
      is untested as such until this task adds it.

## 6. Docs

- [x] 6.1 Update `bloomcli/README.md`: add `[-n/--workers N]` to both commands' synopsis lines
      (`download-for-predict` ~line 392, `batch-download-for-predict` ~lines 424-427) and a short
      option bullet for each (range 1-64, default 8, "1 = sequential"), matching the existing
      `### Download speed (`--workers`)` subsection already documenting this exact flag for
      `cyl download` and `plate download` (README ~lines 194-206: prose + example commands +
      range/ceiling rationale) — scaled down to a bullet appropriate for this section's existing
      bullet-list format, not the `--lock-staleness-seconds` bullet (a different, less directly
      analogous precedent).
- [x] 6.2 Add a `bloomcli/CHANGELOG.md` `[Unreleased]` entry describing the `-n`/`--workers`
      option on both commands, the `download_to`/`fetch_all` reuse, and the new disk-full
      protection (including at `--workers 1`, where none existed before).
- [x] 6.3 Run `uvx ruff check` and `uvx ruff format --check` on
      `bloomcli/src/bloomctl/cyl/download_for_predict.py` and the new test file; fix any findings.
- [x] 6.4 Run the full `bloomcli` test suite (`uv run --extra test pytest`) and confirm no
      regressions beyond this repo's known pre-existing Windows-environment failures (POSIX
      permission bits, symlinks) unrelated to this change.
