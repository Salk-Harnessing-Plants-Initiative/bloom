TDD throughout: write the failing test first, confirm RED, then implement to GREEN. This is a
staging-first, protected repo — every pushed commit must keep CI green, so new tests and the code
they exercise land **in the same commit**. Do not touch `download-for-predict` (the single-scan
command) or its existing tests — this change only modifies `batch-download-for-predict` and adds
a new shared lock module.

**Section dependencies** (corrected during review — see design.md for why): Section 3
(`_locks.py`) has no import dependency on section 2 (the contracts pin bump) — it only uses `os`,
`json`, and `time`, never `sleap_roots_contracts`. Section 2 is a hard prerequisite **only** for
section 5, which imports `RunManifest`. Section 4 (per-scan lock wiring) depends on section 3
(imports `acquire_lock`/`LockContendedError`) but not on section 2. Section 5 depends on **both**
section 2 (`RunManifest`) and section 3 (the manifest lock) — and, because task 5.15 asserts the
staleness option threads through the per-scan lock specifically (task 5.16 covers the manifest
lock's own threading independently), section 5 also depends on section 4 having already landed for
5.15 (there is no per-scan lock to assert against otherwise). A reasonable land order is therefore
2 → 3 → 4 → 5, even though 2 and 3 could in principle land in either order relative to each other.
Section 4's GREEN task (4.7) and section 5's GREEN task (5.17) also share a signature change: per
design.md's "`--lock-staleness-seconds` threads through" decision, `stage_one_scan` gains a new
`staleness_seconds` parameter, threaded from `batch_download_for_predict`'s new CLI option — task
4.7 adds the parameter (defaulting it to `_locks.py`'s `DEFAULT_LOCK_STALENESS_SECONDS` for
section 4's own tests, which land before the CLI option exists), and task 5.17 is what actually
wires the CLI option's value into that parameter at the call site.

Each numbered section below (2, 3, 4, 5) is one commit boundary: its RED tasks and its GREEN task
land together. Section 7 ("Verify") is not a commit boundary.

## 1. Proposal & specs

- [x] 1.1 `openspec validate add-cyl-batch-manifest-lock --strict` passes (proposal.md, design.md,
      tasks.md, spec delta already written).

## 2. Dependency pin bump

This section is a version-floor bump, not new logic, so it is deliberately exempt from strict
RED-before-GREEN ordering — there is no meaningful "failing behavior" to assert before the bump
lands, only "the new symbol isn't importable yet," which task 2.3 records as a regression/contract
test confirming the bump actually worked, not a RED test in the usual sense.

- [x] 2.1 Bump `bloomcli/pyproject.toml`'s `sleap-roots-contracts` floor from `>=0.1.0a5` to
      `>=0.1.0a7`.
- [x] 2.2 Regenerate `bloomcli/uv.lock` (`uv lock` in `bloomcli/`) and confirm it resolves
      `sleap-roots-contracts` to `>=0.1.0a7` (confirmed available on PyPI, published 2026-08-04,
      with unchanged transitive deps — `pydantic`/`pyyaml` only — versus the current `0.1.0a5`
      lock).
- [x] 2.3 Add `bloomcli/tests/test_contracts_pin.py` (new, dedicated file — this is a generic
      dependency-availability check, not batch-command behavior, so it doesn't belong alongside
      `test_cyl_download_for_predict.py`'s CLI tests) asserting `from sleap_roots_contracts import
      RunManifest, RUN_MANIFEST_FILENAME` succeeds; `RunManifest(pipeline_run_id="x",
      scan_keys=["scan_1"])` round-trips both fields; `RunManifest(pipeline_run_id="x",
      scan_keys=[])` raises (the empty-`scan_keys` validator — this is the contract behavior
      section 5's empty-manifest handling, task 5.12, has to work around); and
      `RUN_MANIFEST_FILENAME == "run_manifest.json"` (pins the literal so a future change to the
      constant's value is a visible, deliberate test update here, not silent). Confirm this fails
      against the pre-bump pin (mentally or by temporarily reverting 2.1-2.2) before considering
      2.1-2.2 the fix for it.
- [x] 2.4 Confirm 2.3 passes once 2.1-2.2 land.

## 3. `_locks.py` — shared lock/lease primitive

- [x] 3.1 RED: `bloomcli/tests/test_cyl_locks.py` — acquiring a lock on a path with no existing
      lock file succeeds; the lock file exists while the `with` block's body runs and contains
      the acquiring process's pid and an `acquired_at` timestamp.
- [x] 3.2 RED: the lock file no longer exists once the `with` block exits normally.
- [x] 3.3 RED: the lock file no longer exists even if the `with` block's body raises an exception
      (the exception still propagates to the caller).
- [x] 3.4 RED: acquiring a lock whose file already exists and is **not stale** (age below the
      staleness threshold) raises `LockContendedError`, and the message names the existing
      holder's pid and the lock's age.
- [x] 3.5 RED: acquiring a lock whose file already exists and has an age **strictly greater than**
      the staleness threshold reclaims it — succeeds, and the reclaimed file's contents (pid,
      `acquired_at`) belong to the new acquirer, not the stale one.
- [x] 3.6 RED: acquiring a lock whose file already exists with an age **exactly equal to** the
      staleness threshold is treated as still contended (not stale) — raises
      `LockContendedError`, the same as 3.4. This pins down the boundary operator explicitly (an
      earlier draft of this proposal had design.md and tasks.md disagreeing on `>` vs `>=` at this
      exact boundary — this task is what prevents that ambiguity from reaching implementation).
      Real wall-clock time makes hitting an *exact* age boundary racy/flaky to assert directly, so
      this test monkeypatches `_locks.time.time` (the module's own `import time; time.time()` call,
      not a `from time import time` that couldn't be patched this way) to a fixed value, constructs
      the lock file's `acquired_at` at exactly `fixed_time - staleness_seconds`, and asserts against
      that same fixed time — not real elapsed time.
- [x] 3.7 RED: two lock acquisitions on two different paths never contend with each other
      (acquiring path A while path B's lock is held succeeds immediately).
- [x] 3.8 RED: acquiring a lock closes the file descriptor **before** the guarded code's body
      runs — assert via monkeypatching `os.close` to record a call, and a sentinel set inside the
      `with` block, then assert `os.close` was called before the sentinel was set. This is the
      one test in this section that isn't provable by an end-to-end deletion test: this repo's CI
      runners are Linux-only, and POSIX allows unlinking an open file unconditionally, so a test
      that merely deletes the lock file while the `with` block is open would pass on CI regardless
      of whether the implementation closes the fd — it would only fail on a Windows dev machine
      (this repo's actual local dev environment), where an open handle blocks deletion. Asserting
      the close call's ordering directly is what catches the bug without a Windows runner.
- [x] 3.9 RED: acquiring a lock whose parent directory does not exist yet creates it
      (`out_dir/.locks/` didn't exist before the call) and succeeds — without this, the very first
      lock acquisition against a brand-new `out_dir` would raise `FileNotFoundError`, not
      `FileExistsError`, an unhandled case in an earlier draft of this design.
- [x] 3.10 RED: reclaiming a stale lock re-reads the lock file's content immediately before
      unlinking it, and if that content has changed since it was first judged stale (simulate by
      monkeypatching the re-read to return a *different*, fresh `acquired_at` the second time it's
      called), `acquire_lock` raises `LockContendedError` instead of unlinking what is now a live
      lock. This is the test proving the re-read-before-unlink mitigation in design.md's "Lock
      mechanism" section actually works — without it, a reclaimer can delete a peer's freshly
      re-acquired live lock (see design.md for the full race trace this closes).
- [x] 3.11 RED: reclaiming a stale lock whose file has already been removed by a competing
      reclaimer by the time this process attempts its own re-read or `unlink()` call — asserted via
      monkeypatching the re-read (or `os.unlink`) to raise `FileNotFoundError` — results in
      `LockContendedError`, not an unhandled `FileNotFoundError` propagating to the caller.
- [x] 3.12 GREEN: implement `bloomcli/src/bloomctl/cyl/_locks.py` — `LockContendedError`, a
      module-level `DEFAULT_LOCK_STALENESS_SECONDS = 900` constant (the value section 4's tests
      and the section 5 CLI option's own default both use, per design.md's "threads through"
      decision), and an `acquire_lock(path, *, staleness_seconds)` context manager per design.md's
      "Lock mechanism" section (`os.open(..., O_CREAT | O_EXCL | O_WRONLY)` for atomic acquire;
      immediate `os.close` before yielding; parent-directory creation; content-based
      (`acquired_at`, not mtime) staleness with a strict `>` comparison; re-read-before-unlink on
      reclaim, treating a changed or missing file as contention rather than proceeding to unlink;
      unconditional removal on exit).

## 4. Per-scan lock wired into `stage_one_scan`

- [x] 4.1 RED: staging a scan whose `out_dir/.locks/{scan_key}.lock` is currently held (not
      stale, simulated by creating the lock file directly in the test) returns
      `ScanResult(scan_key, "failed", ...)` naming the holder's pid and lock age — and does not
      touch that scan's frames or sidecar (assert the fake storage client's download is never
      called for it).
- [x] 4.2 RED: staging a scan whose lock file exists but has an age **strictly greater than** the
      staleness threshold (constructed directly in the test, via its `acquired_at` JSON field, not
      via `os.utime`/mtime) proceeds normally — the scan stages (or skips) exactly as it would
      with no lock file present at all.
- [x] 4.3 RED: staging a scan with no lock file present behaves identically to before this change
      for every existing `stage_one_scan` outcome (not found, zero frames, invalid frame_numbers,
      metadata-resolution failure, partial frame-download failure, already-staged/skip, full
      success) — regression coverage proving the lock wiring is transparent to every case
      `test_cyl_download_for_predict.py` already covers.
- [x] 4.4 RED: after a scan stages successfully, `out_dir/.locks/{scan_key}.lock` no longer exists
      (the lock was released, not left behind).
- [x] 4.5 RED: a batch with scan_ids `[1, 2]` where `scan_1`'s lock is held (not stale) and
      `scan_2` has no lock — `scan_1` is reported `failed` (contention), `scan_2` stages
      successfully, and the command's overall exit code is non-zero (existing "any failure ⇒
      non-zero" behavior, exercised here via a lock-contention failure specifically).
- [x] 4.6 RED: staging the very first scan into a brand-new `out_dir` (no `.locks/` directory yet
      at all) succeeds without a `FileNotFoundError` — the end-to-end regression counterpart of
      task 3.9's unit test, confirmed through the actual `stage_one_scan` call path.
- [x] 4.7 GREEN: wire `out_dir/.locks/{scan_key}.lock` into `stage_one_scan` per design.md,
      catching `LockContendedError` and mapping it to a `failed` `ScanResult` rather than letting
      it propagate (consistent with `stage_one_scan`'s existing catch-all isolation, task 9.2 of
      the sibling `add-cyl-batch-commands` change).

## 5. RunManifest write + merge in `batch_download_for_predict`

- [x] 5.1 RED: a batch where every scan succeeds writes `out_dir / RUN_MANIFEST_FILENAME`
      (imported from `sleap_roots_contracts`, not a bloomctl-local literal — assert the test
      itself reads via the imported constant, not a hardcoded `"run_manifest.json"` string, so a
      future rename of the constant breaks this test visibly instead of silently passing against
      a stale literal) with `scan_keys` exactly equal to every staged scan_key.
- [x] 5.2 RED: a batch with one failed scan among several excludes that scan's key from the
      written manifest's `scan_keys`.
- [x] 5.3 RED: a batch where one scan is `skipped` (already staged) includes that scan's key in
      the written manifest's `scan_keys`.
- [x] 5.4 RED: with `ARGO_WORKFLOW_NAME` set (via `monkeypatch.setenv`), the written manifest's
      `pipeline_run_id` equals that value.
- [x] 5.5 RED: with `ARGO_WORKFLOW_NAME` unset (via `monkeypatch.delenv`), the command does not
      raise, and the written manifest's `pipeline_run_id` matches `local-[0-9a-f]{8}`.
- [x] 5.6 RED: running the command twice in a row, both times with `ARGO_WORKFLOW_NAME` unset,
      produces two different `pipeline_run_id` values across the two runs — read and record the
      manifest's `pipeline_run_id` after the *first* invocation completes, before running the
      second (since `pipeline_run_id` is last-writer-wins, reading the manifest only after both
      runs would show just the second value and could never actually distinguish them).
- [x] 5.7 RED: a second invocation with a scan_ids set disjoint from a pre-existing manifest's
      `scan_keys` merges — the rewritten manifest's `scan_keys` is the union of both, not just the
      second invocation's, and `pipeline_run_id` reflects the second (latest) invocation.
- [x] 5.8 RED: a second invocation with a scan_ids set that overlaps or repeats a pre-existing
      manifest's `scan_keys` still produces the correct union with no duplicate entries in
      `scan_keys` (e.g. existing `["scan_1", "scan_2"]` + this run's `[2, 3]` → exactly
      `["scan_1", "scan_2", "scan_3"]`).
- [x] 5.9 RED: manifest-lock contention (simulate by creating `out_dir/.locks/manifest.lock`
      directly in the test, not stale) causes the command to exit non-zero with an actionable
      error, while any pre-existing `RUN_MANIFEST_FILENAME` file remains intact and parseable
      afterward (read it back and confirm it still parses to its pre-test contents).
- [x] 5.10 RED: manifest-lock contention as in 5.9, but with **no** pre-existing manifest file at
      all (no prior invocation ever wrote one) — the command still exits non-zero with an
      actionable error, and no partial/corrupt manifest file is created.
- [x] 5.11 RED: an existing `RUN_MANIFEST_FILENAME` file that is not valid JSON (or doesn't parse
      to a `RunManifest` shape) causes the write step to fail with an actionable error — the
      corrupt file is not silently overwritten with only this invocation's `scan_keys` (read it
      back afterward and confirm its corrupt contents are unchanged).
- [x] 5.12 RED: a batch where every scan fails, and no pre-existing manifest is present in
      `out_dir` — the command still exits non-zero (existing all-failed behavior), does not raise
      an unhandled `pydantic.ValidationError` from constructing `RunManifest(scan_keys=[])`, and
      does not create a manifest file at all (nothing usable to record — see design.md's "An
      empty scan_keys result" decision).
- [x] 5.13 RED: a stale (age strictly greater than the threshold) `out_dir/.locks/manifest.lock`
      is reclaimed rather than blocking the write — mirrors task 4.2 for the manifest lock
      specifically.
- [x] 5.14 RED: after a successful manifest write, `out_dir/.locks/manifest.lock` no longer exists
      — mirrors task 4.4 for the manifest lock specifically.
- [x] 5.15 RED: `--lock-staleness-seconds` threads through to the **per-scan** lock — a
      `scan_key`'s lock file constructed with an age older than a custom, shorter threshold (e.g.
      `--lock-staleness-seconds 5` with a 10-second-old lock) is reclaimed by `stage_one_scan`
      rather than treated as contended. (Requires section 4 to have already landed — this is the
      one place section 5 has a real dependency on section 4, not just a review-ordering
      preference.)
- [x] 5.16 RED: `--lock-staleness-seconds` threads through to the **manifest** lock — the same
      custom threshold reclaims a stale `manifest.lock` under the identical rule.
- [x] 5.17 GREEN: implement the manifest write/merge in `batch_download_for_predict` (compute
      `scan_keys` from the batch's `ok`/`skipped` results, resolve `pipeline_run_id`, acquire the
      manifest lock, read-merge-write `out_dir / RUN_MANIFEST_FILENAME` via
      `RunManifest`/`atomic_write_bytes` — skipping the write entirely when the merged `scan_keys`
      would be empty, and raising a `click.ClickException` on a corrupt existing manifest rather
      than treating it as absent) and add the `--lock-staleness-seconds` option, threading it into
      `stage_one_scan`'s new `staleness_seconds` parameter.
- [x] 5.18 Confirm (no new production code expected — this exercises 5.17's merge logic in a new
      arrangement, not a new behavior) that a simulated mid-batch crash is healed by an orchestrator
      retry: call `stage_one_scan` directly for scan_ids `[1, 2, 3]` (bypassing
      `batch_download_for_predict`'s manifest-write step entirely, standing in for a process killed
      before that step ran) so 1-3 have valid sidecars on disk but no manifest exists yet; then run
      the real `batch-download-for-predict` command with `scan_ids=[1, 2, 3, 4, 5]` — assert the
      resulting manifest's `scan_keys` includes all five (1-3 via skip-check as `skipped`, 4-5
      freshly staged as `ok`), confirming design.md's "Risks/Trade-offs" claim that a same-scan_ids
      retry closes the mid-batch-crash gap actually holds.

## 6. Docs & changelog

- [x] 6.1 Add an entry under `[Unreleased]` in `bloomcli/CHANGELOG.md` describing the
      `RunManifest` write, the per-scan and manifest locks, and the `--lock-staleness-seconds`
      option — matching the existing entries' level of detail; reference bloom #653; also note
      the `sleap-roots-contracts` floor bump to `>=0.1.0a7` (matching the `[0.1.0a2]` entry's own
      precedent of calling out a contract-floor bump explicitly).
- [x] 6.2 Update `bloomcli/README.md`'s `batch-download-for-predict` section (add one if none
      exists) to document the manifest file, the lock files under `.locks/`, and the new option —
      including adding `[--lock-staleness-seconds N]` to the command's synopsis block, not just
      prose.
- [x] 6.3 In `bloomcli/src/bloomctl/cyl/download_for_predict.py`: update
      `batch_download_for_predict`'s docstring (this is Click's `--help` text, seen directly by
      users) to describe all three additions — the `RunManifest` write/merge, the per-scan and
      manifest locks, and `--lock-staleness-seconds`. Update `stage_one_scan`'s docstring
      separately to describe only what it actually does — the per-scan lock around its
      skip-check-through-sidecar-write sequence — since it has no involvement in the manifest
      write at all.
- [x] 6.4 Add a `_locks.py` line to `bloomcli/src/bloomctl/cyl/__init__.py`'s module-docstring
      "one file per entity" command catalog, matching the existing `_batch.py` line's style (a
      shared helper module with no CLI of its own).

## 7. Verify

- [x] 7.1 `uv run --extra test pytest` green in `bloomcli/` — full suite, not just the new/changed
      files.
- [x] 7.2 `uvx ruff@0.9.9 check bloomcli/` clean. Note the precise gap this closes: the local
      pre-commit `ruff` hook's `files:` pattern already includes `bloomcli/`, so `ruff check --fix`
      *does* run for contributors with the hook installed — but no CI workflow invokes
      `pre-commit run` at all, and `.pre-commit-config.yaml`'s `ruff-format`/`black` hooks do
      exclude `bloomcli/`. The only CI-enforced ruff gate is `release-bloomcli.yml`'s
      `uvx ruff@0.9.9 check .`, which runs after merge at release-cut time — so a contributor
      without the local hook installed gets no lint signal on this PR unless this step is run
      manually before pushing.
- [x] 7.3 `cd bloomcli && uv export --frozen --no-hashes | uvx pip-audit@2.10.0 -r /dev/stdin`
      clean locally before opening the PR — this CI job has no `--ignore-vuln` escape hatch for
      `bloomcli` specifically, so a surprise finding here is cheaper to catch locally first.
- [x] 7.4 `/review-openspec` before requesting approval; fix any findings.
- [x] 7.5 `/pre-merge` before opening the PR; fix any findings. This change touches only
      `bloomcli/`, so the applicable subset was run: full pytest suite (green, 6 pre-existing
      environment-specific failures unrelated to this change — confirmed by re-running them
      against the unmodified tree), the `bloomcli` Docker image build (succeeds, resolves
      `sleap-roots-contracts==0.1.0a7`) and `--version`/`--help` smoke test, ruff, and pip-audit.
- [x] 7.6 Open the PR against `staging` — [PR #655](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/655).
      Still needed: a non-author approving review (branch protection has `enforce_admins=true`
      on `staging`) before merge.
- [x] 7.7 Manually verified against the real `pipeline-staging` Supabase profile (not mocks),
      per the reviewers' own repeated note that the mocked test suite can't exercise real
      network I/O or genuine OS-level process races:
      - `batch-download-for-predict` against 3 real scans (289, 577, 1009) — all staged, correct
        `run_manifest.json` written.
      - Re-running with one already-staged scan — reported `skipped`, not re-downloaded.
      - A nonexistent scan_id — excluded from the manifest, other scans unaffected.
      - Two separate real invocations with disjoint scan sets into one `out_dir` — manifest
        correctly merged to the union, `pipeline_run_id` updated to the latest invocation's.
      - **Two actual `bloomctl` processes launched simultaneously** against the same scan_id and
        `out_dir` (real OS-level race, not simulated timing) — one won and staged normally, the
        other got a clean `failed` result naming the holder's pid and lock age; `.locks/` empty
        afterward, no corruption on disk. This is the exact bloom #533 scenario this whole
        feature exists to prevent, now confirmed closed against genuinely concurrent processes.
      - A hand-planted stale lock (fake dead pid, `acquired_at` far in the past) — correctly
        reclaimed; the scan staged normally, no `LockContendedError`.
      - `--lock-staleness-seconds nan` and `0` — both rejected by a clean `click.UsageError`
        before any network call, matching the unit-tested behavior exactly.
- [ ] 7.8 After merge: tick the `bloomctl` row in `sleap-roots-pipeline`'s
      `docs/bloom-integration/roadmap.md` ("Cross-repo correctness: manifest-scoped processing"
      section) per that repo's own close-the-loop convention — a follow-up action in that repo,
      not a task in this one.
- [ ] 7.9 Run `openspec archive add-cyl-batch-manifest-lock` once deployed.

## 8. Post-PR-review hardening (found via `/review-pr` on #655, fixed same PR)

`/review-pr` ran 5 parallel subagents against PR #655. Two independent agents traced the same
BLOCKING bug from different angles (an unrecoverable corrupt lock on a write failure during
acquire; a slow-holder's lock being reclaimed and then that holder's own release deleting the
new holder's live lock) — both fixed here, in `_locks.py`. See design.md's new "Post-PR-review
hardening" and "Risks/Trade-offs" entries for the full reasoning, including the residual
"slow-but-alive holder can still be initially reclaimed" risk that the release-path fix narrows
but does not eliminate (documented, not code-fixed — a proper fix needs lease renewal, out of
scope for a review-response pass).

- [x] 8.1 RED: `bloomcli/tests/test_cyl_locks.py` — an `OSError` during the lock body's write
      (after the exclusive create succeeds) does not leave the lock file behind; a fresh
      `acquire_lock` on the same path immediately afterward succeeds (no permanently
      unreclaimable lock from an interrupted write, at any `staleness_seconds`).
- [x] 8.2 RED: if the lock file is reclaimed by another process (different `pid`) while this
      process is still inside its own `with acquire_lock(...)` block, this process's own release
      does NOT delete the peer's now-live lock file.
- [x] 8.3 RED: a stale-lock reclaim whose final `unlink()` raises `PermissionError` (not just
      `FileNotFoundError` — the case a competing process's file handle overlaps the unlink,
      observed on this repo's own Windows dev platform) raises `LockContendedError`, not an
      unhandled `PermissionError`.
- [x] 8.4 GREEN: `_locks.py` — split lock-file creation and body-write into
      `_create_lock_file`/`_write_lock_body`, cleaning up (unlinking) the just-created file if
      the write itself fails before re-raising; add a `_release` helper that only unlinks the
      lock file if it still records this process's own pid; widen the reclaim path's final
      `unlink()` exception handling to `(FileNotFoundError, PermissionError)`.
- [x] 8.5 RED: `write_run_manifest`'s manifest write raising a plain `OSError` (e.g. simulating
      disk-full via a mock that fails only for the manifest path specifically, not per-frame
      writes, which also go through `atomic_write_bytes`) surfaces as a clean
      `click.ClickException` (`isinstance(result.exception, SystemExit)` under `CliRunner`, not
      the raw `OSError` instance — confirmed empirically that click normalizes both
      `ClickException` and `ctx.exit()` to `SystemExit` in `CliRunner`'s `result.exception`,
      while a genuinely unhandled exception surfaces as itself).
- [x] 8.6 GREEN: widen `write_run_manifest`'s `except LockContendedError` to
      `except (LockContendedError, OSError)`.
- [x] 8.7 RED: strengthened the 4 existing manifest-lock-contention/corruption/all-failed tests
      that only asserted `exit_code != 0` (vacuous — couldn't distinguish a clean
      `click.ClickException` from an unhandled crash, confirmed by deliberately breaking each
      guard and re-running: the tests still passed) to also assert
      `isinstance(result.exception, SystemExit)`.
- [x] 8.8 RED: `--lock-staleness-seconds 0` and a negative value are both rejected by the CLI
      (previously silently accepted and defeated the entire locking feature — confirmed
      empirically that `age <= staleness_seconds` is false for essentially any lock, however
      freshly held, once the threshold is `<= 0`).
- [x] 8.9 GREEN: `--lock-staleness-seconds` now uses `click.FloatRange(min=0, min_open=True)`.
- [x] 8.10 Promoted the `.locks`/`manifest.lock` string literals (previously repeated at each of
      the two call sites in `download_for_predict.py`) to named constants (`LOCKS_DIRNAME`,
      `MANIFEST_LOCK_FILENAME`) in `_locks.py` — no test needed (pure refactor, existing tests
      cover the resulting paths unchanged).
- [x] 8.11 Full suite green (583 passed, 6 skipped, the same 6 pre-existing environment-specific
      failures unrelated to this change), ruff clean, `openspec validate --strict` passes.
- [x] 8.12 Documented, not code-fixed (see design.md's Risks/Trade-offs): the residual
      slow-but-alive-holder reclaim risk, the no-migration-path-for-pre-existing-`out_dir`s gap
      (a cross-repo follow-up needed before any consumer trusts the manifest exclusively), and
      `scan_is_already_staged` not re-verifying frame files exist. None block this PR; each is a
      candidate follow-up issue to file once a downstream consumer of the manifest exists.

## 9. Round 2 post-review hardening (found on a second `/review-pr` pass on #655, fixed same PR)

A second review round specifically re-probed whether section 8's fixes were themselves correct.
Two agents independently found that round 1's own release fix ("unreadable = treat as ours,
delete it") reopened the identical failure class it was written to close, via a different
trigger — worth reading design.md's new "Round 2 post-review hardening" entry for the full
pattern (fixing one trigger without enumerating every trigger for the same failure mode).

- [x] 9.1 RED: `bloomcli/tests/test_cyl_locks.py` — if a peer's fresh reclaim (its
      `_create_lock_file` succeeded, its `_write_lock_body` hasn't run yet) leaves a transiently
      empty/unreadable lock file, and *this* process's release runs in that exact window,
      release must NOT delete it. Round 1's `_release` treated "unreadable" as "still ours,
      delete" — unsafe, since the file is more plausibly a peer's in-flight write than genuine
      self-corruption.
- [x] 9.2 GREEN: `_release` now only deletes on a *positively confirmed* pid match; unreadable
      or different-pid content is left alone unconditionally.
- [x] 9.3 RED: if `_write_lock_body`'s cleanup-on-write-failure's own `os.close(fd)` call also
      raises, the nested unlink-and-reraise cleanup must still run (previously that `os.close`
      was unguarded, so its own failure would skip cleanup entirely and mask the original
      write-failure exception — reopening "unreadable, permanently unreclaimable lock file"
      through close failing instead of write failing).
- [x] 9.4 RED: a short write (`os.write` returning fewer bytes than requested without raising —
      POSIX-permitted, e.g. under signal interruption) is treated as a failure, taking the same
      cleanup path as a raised exception — previously the return value was never checked, so a
      short write would silently leave a truncated, unparseable lock body on disk (the same
      failure class again, via a return value nobody was checking).
- [x] 9.5 GREEN: `_write_lock_body`'s cleanup path now guards `os.close` with its own
      `try/except OSError: pass` before attempting the unlink and re-raising, and explicitly
      raises on a short write before that cleanup path, so both 9.3 and 9.4 take the identical,
      already-hardened cleanup route.
- [x] 9.6 RED: `bloomcli/tests/test_cyl_locks.py` — mirroring `test_lock_fd_is_closed_before_
      guarded_code_runs`'s own approach for the success path, an explicit call-order assertion
      (not an end-to-end deletion check, which can pass on Windows for the wrong reason) proves
      `close` happens before `unlink` on the write-failure cleanup path — the platform this
      suite's CI actually runs on (Linux) permits unlinking an open fd regardless of ordering, so
      only an explicit order assertion catches a regression here.
- [x] 9.7 RED: `acquire_lock(path, staleness_seconds=float("nan"))` (and `inf`/`-inf`) raises
      `ValueError` — `_locks.py` is documented as a generic, reusable primitive (bloom #481), so
      it must reject this itself, not rely solely on the CLI's own validation.
- [x] 9.8 GREEN: `acquire_lock` now validates `staleness_seconds` is finite and positive
      (`math.isfinite`) before doing anything else, raising `ValueError` if not.
- [x] 9.9 RED: `bloomcli/tests/test_cyl_download_for_predict.py` —
      `--lock-staleness-seconds nan` is rejected by the CLI itself (`click.FloatRange(min=0,
      min_open=True)` was found to let `nan` straight through, since NaN comparisons are always
      `False` — a third way, after `0` and negative values, to reach the same "staleness check
      silently defeated" outcome).
- [x] 9.10 GREEN: `batch_download_for_predict` now explicitly rejects non-finite
      `--lock-staleness-seconds` values via `click.UsageError` before any work starts (not only
      via `acquire_lock`'s own defense-in-depth `ValueError`, which would otherwise surface late
      and inconsistently — masked as a per-scan failure by `stage_one_scan`'s catch-all, or as a
      raw exception from `write_run_manifest`, rather than one clean, immediate usage error).
- [x] 9.11 RED: a `pydantic.ValidationError` from constructing `RunManifest(...)` itself (not
      just from `model_validate_json` on an existing corrupt file) is caught and converted to a
      clean `click.ClickException` — simulated by monkeypatching `RunManifest` directly, since
      this path is otherwise practically unreachable given `merged_scan_keys`'s deduplication
      and `scan_key_for()`'s fixed format.
- [x] 9.12 GREEN: `write_run_manifest`'s except clause widened to
      `(LockContendedError, OSError, ValidationError)`.
- [x] 9.13 Corrected a stale cross-reference in design.md's "Lock mechanism" section: the
      original "Accepted limitation" paragraph (about the reclaim-vs-reclaim race on an
      already-stale lock) could read, in isolation, as covering the full risk picture — it
      doesn't; the materially larger slow-but-alive-holder-gets-reclaimed-at-all risk is
      documented separately in Risks/Trade-offs, now cross-referenced from both directions.
      Also elevated that risk's language from "a reasonable follow-up if this proves
      insufficient in practice" to explicitly recommending lease-renewal as a near-term
      follow-up, given a second reviewer traced a concrete, silent (non-erroring) data-integrity
      consequence — not just wasted duplicate work — if the reclaim-of-a-live-lock scenario is
      ever actually hit.
- [x] 9.14 Full suite green (590 passed, 6 skipped, the same 6 pre-existing environment-specific
      failures unrelated to this change), ruff clean, `openspec validate --strict` passes.
- [ ] 9.15 Still not code-fixed, by design (see design.md): lease-renewal itself (the actual fix
      for "a slow-but-alive holder's lock can still be initially reclaimed," as opposed to round
      1+2's fixes, which only stop that reclaim from cascading into further corruption) —
      recommended as a near-term follow-up, not a blocking requirement for this PR, since no live
      caller submits concurrently yet.
