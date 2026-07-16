# Tasks — bloomctl cyl download-for-predict (A4 stage-in)

TDD throughout: write the failing test first, confirm RED, then implement to GREEN. This is a
staging-first, protected repo — every pushed commit must keep CI green, so new tests and the code
they exercise land **in the same commit** (no standalone red-tests commit). Unit tests use
`click.testing.CliRunner` + `monkeypatch` fakes, mirroring `bloomcli/tests/test_download_scan.py`
and `test_cyl_ingest.py`. The module structure mirrors `download.py` and `ingest.py` (pure helpers
above the `# --- supabase / storage I/O ---` marker).

CI note (verified from `add-cyl-ingest-cli` tasks): `.github/workflows/pr-checks.yml` runs
`cd bloomcli && uv run --extra test pytest tests/ -m "not integration"` — bloomcli has no
`uv.lock`, is excluded from `check-uv-locks.py` and `pip-audit`, and is outside the black/ruff
pre-commit globs (ruff runs only at release). The `-m "not integration"` flag is already in
place.

Proposal + implementation land in the **same PR** (bloom #411).

**Commit plan (reconciled after `/review-openspec`)** — §3-5's RED tasks cannot be their own
commits before §6's GREEN implementation without going red on every intermediate push (an
unregistered command fails CLI-invocation tests outright, not silently). Landed as (commits 3+4
merged during implementation — the reviewer noted this split was optional, not CI-mandatory,
since one module file + its full test suite + registration is a single coherent, already-GREEN
unit; see round-1 git-workflow review):

1. `docs(openspec): add proposal for cyl download-for-predict (#411)` — §1 (this proposal).
2. `chore(contracts): bump sleap-roots-contracts to v0.1.0a4 (bloomcli floor + full re-pin)` —
   §2, verified installable (2.1) and re-pin diff confirmed `$id`-only (2.2) before committing.
3. `feat(bloomctl): add cyl download-for-predict command (#411)` — §3-§6 (oracle test, pure
   helpers, command-wiring tests, implementation, and registration together).
4. `docs(bloomcli): document cyl download-for-predict command` — §7.
   §8 (verify) is a gate applied across all commits during review, not its own commit.

## 1. Proposal & specs (this change)

- [x] 1.1 Author `proposal.md`, `design.md`, and the `cyl-download-for-predict` spec delta.
      Run `openspec validate add-cyl-download-for-predict --strict` and resolve all issues.

## 2. Package + repo setup (pre-req; lands before tests that import it)

- [x] 2.1 Bump `sleap-roots-contracts>=0.1.0a3` → `>=0.1.0a4` in `bloomcli/pyproject.toml`.
      Verify installability: `uv run --no-project --with 'sleap-roots-contracts>=0.1.0a4' python -c
"from sleap_roots_contracts import resolve_params; print('ok')"`. bloomcli has no `uv.lock` —
      no lock refresh needed.
- [x] 2.2 Full contracts re-pin per contracts PR #16 instructions:
      (a) update `contracts/pin.json`: version `v0.1.0a3` → `v0.1.0a4`, `$id` and `source` URLs;
      (b) fetch updated `contracts/schema/result_envelope.schema.json` from
      `github.com/talmolab/sleap-roots-contracts` at tag `v0.1.0a4` and **diff it against the
      currently-vendored `a3` schema with the version string normalized out** — do not assume
      the diff shape in advance (the prior two re-pins, `a1→a2` and `a2→a3`, were both real
      revisions, not `$id`-only no-ops). Confirmed during review: for `a4` this diff IS
      `$id`-only;
      (c) regenerate `contracts/generated/result-envelope.ts` from the updated schema
      (`npm run contracts:gen`, per `contracts/README.md`'s re-pin procedure) and run
      `npm run contracts:check` locally to confirm it passes before committing;
      (d) update `contracts/README.md`: bump the "Currently pinned: `v0.1.0a3`" line to
      `v0.1.0a4`, and add a new dated "> Note on `v0.1.0a4`: ..." paragraph above the existing
      `v0.1.0a3` note (matching that note's structure) explaining that this re-pin is an
      `$id`-only structural no-op for the JSON Schema, and that the substantive addition
      (`resolve_params`) is on the Python package side, not the schema.

## 3. RED — oracle test (write FIRST; must fail before implementation)

Write the following test in `bloomcli/tests/test_cyl_download_for_predict.py`. It must be RED
before any implementation code exists.

**Not a CI gate (reconciled after `/review-openspec`).** `sleap-roots-predict` is not published
to PyPI and its `pyproject.toml` currently exact-pins `sleap-roots-contracts==0.1.0a3`, which
conflicts with this proposal's `>=0.1.0a4` floor — adding it as a bloomcli test dependency today
would break dependency resolution outright. Its own re-pin to the newer contracts version is
in progress separately (per Elizabeth). Until that lands and `sleap-roots-predict` can be added
as a git dependency, this test is `pytest.importorskip`-guarded and **will always self-skip in
CI**; run it manually on a dev machine with `sleap-roots-predict` installed via its own `uv`
extra (`uv run --with "sleap-roots-predict[cpu] @ file:///path/to/sleap-roots-predict" --extra
test pytest ...` from `bloomcli/`, or `cd sleap-roots-predict && uv sync --extra cpu` for a
standalone env) before merge — this repo's convention is `uv`, not a hand-maintained conda env.
Tasks 4.x/5.x below independently assert
every shape fact predict's code is documented to require, without importing predict — that suite,
not this test, is the CI-enforced contract for this change.

- [x] 3.1 **Oracle / acceptance test (manual, dev-machine only — see note above):** Given the
      SCAN fixture row (from `test_download_metadata.py`), a list of two fake `cyl_images` rows
      (`id=1001, frame_number=0, object_path="cyl-images/a.png"` and
      `id=1002, frame_number=1, object_path="cyl-images/b.png"`), and fake frame bytes per image:
  - `build_sidecar(scan, images, frame_bytes)` returns a dict whose `scan_key` equals `"scan_1"`
    (scan_id=1 from SCAN fixture);
  - the dict's `params` has keys `species`, `mode`, `age`; `mode` is `"cylinder"`;
  - the dict's `image_ids` is `[1001, 1002]`;
  - the dict's `images_checksum` starts with `"sha256:"`;
  - **cross-repo oracle**: write the sidecar to `tmp_path/scan_1/scan_1.scan_metadata.json`
    alongside two fake image files, then assert `sleap_roots_predict.discover_scans(tmp_path)`
    returns exactly one `ScanInput` with `scan_key="scan_1"`, `error=None`, and non-None `params`
    (`pytest.importorskip("sleap_roots_predict")` at the top of the test).

## 4. RED — pure helpers (`bloomcli/tests/test_cyl_download_for_predict.py`)

Each test must FAIL before `download_for_predict.py` exists.

- [x] 4.1 `scan_key_for(scan_id)` returns `f"scan_{scan_id}"` for integer and string inputs.
- [x] 4.2 `frame_dest_for_predict(scan_dir, image)` returns
      `scan_dir / f"{image['frame_number']}{suffix}"` where suffix is the original extension of
      `image['object_path']` (or `.png` when missing).
- [x] 4.3 `compute_checksum(frame_bytes_list)` returns a string starting with `"sha256:"` whose
      hex suffix is the sha256 of all bytes concatenated in list order; an empty list produces a
      well-defined checksum (sha256 of `b""`).
- [x] 4.4 `build_sidecar(scan, images, frame_bytes_list)` assembles the sidecar dict — all four
      fields present (`scan_key`, `params`, `image_ids`, `images_checksum`); `image_ids` order
      matches the input `images` list order; `scan_key` equals `scan_key_for(scan["scan_id"])`.
- [x] 4.5 `build_sidecar` calls `resolve_params(scan, overrides={"mode": "cylinder"})` — assert
      the actual call, not just the output (monkeypatch/spy on `resolve_params` to capture the
      `overrides` argument it receives), and assert it is exactly `{"mode": "cylinder"}`.
      (Asserting only `params["mode"] == "cylinder"` is insufficient: `sleap-roots-contracts>=0.1.0a4`'s
      `_mode_for_scan` currently ignores its `metadata` argument and unconditionally returns
      `"cylinder"` regardless of how — or whether — `resolve_params` is called, so that assertion
      alone can't distinguish "the override was passed" from "the oracle ignores every caller.")
- [x] 4.5b Add `test_resolve_params_ignores_mode_on_pinned_contracts_version`: call
      `resolve_params(SCAN)` directly (no `overrides` at all) against the pinned
      `sleap-roots-contracts>=0.1.0a4`, and assert `result.values["mode"] == "cylinder"` anyway.
      Documents that `overrides={"mode": "cylinder"}` in `build_sidecar` is not currently
      load-bearing on this contracts version; if a future contracts bump makes `mode`
      metadata-driven, this test starts failing and flags the divergence instead of it going
      unnoticed.
- [x] 4.6 `write_sidecar(sidecar, path)` writes valid UTF-8 JSON to `path` (parent created if
      absent); round-trips back to the original dict via `json.loads`.
- [x] 4.7 `frame_dest_for_predict` with `image["object_path"]` lacking an extension (e.g.
      `"cyl-images/a"`) returns a path ending in `.png` (the documented default).
- [x] 4.8 `compute_checksum` and `build_sidecar` with an empty `images` / `frame_bytes_list`
      produce `image_ids: []` and `images_checksum == "sha256:" + hashlib.sha256(b"").hexdigest()`
      — documents the pure helpers are well-defined on empty input. (Note: this path is not
      actually exercised by 5.9's CLI behavior — the command short-circuits on zero `cyl_images`
      rows before ever calling `build_sidecar`, per spec.md's "zero cyl_images rows" scenario —
      this task exists for the helpers' own contract, independent of that CLI-level guard.)

## 5. RED — command wiring (`bloomcli/tests/test_cyl_download_for_predict.py`)

- [x] 5.1 Confirm `fetch_scan` is reused from `download.py`, not duplicated: import
      `bloomctl.cyl.download_for_predict` and assert `download_for_predict.fetch_scan is
download.fetch_scan` (mirrors the intent of `test_fetch_scan_returns_single_row`, which
      exercises `fetch_scan` itself — this task instead pins the no-duplication contract).
- [x] 5.2 Happy-path CLI: `CliRunner().invoke(cli, ["cyl", "download-for-predict", "1",
str(out)])` with faked `fetch_scan` → SCAN, faked `fetch_images` → two image rows, faked
      Storage bucket, monkeypatched creds/auth → exit 0; assert:

  - `out/scan_1/0.png` and `out/scan_1/1.png` exist;
  - `out/scan_1/scan_1.scan_metadata.json` is valid JSON with `scan_key="scan_1"`,
    `image_ids=[1001, 1002]`, `params.mode="cylinder"`, `images_checksum` starts with `"sha256:"`.

- [x] 5.3 Scan not found: faked `fetch_scan` returns `None` → exit non-zero, "not found" in
      output, no directory created.
- [x] 5.4 Partial frame failure: one frame download raises → exit non-zero, failure count in
      output; assert the successfully-downloaded frame file(s) still exist on disk; assert
      `scan_1.scan_metadata.json` is **not** written (per the design decision: a sidecar is a
      claim that `image_ids`/`images_checksum` match what's on disk — see design.md).
- [x] 5.5 `fetch_scans` (experiment path) is never called in `download-for-predict` — assert via
      monkeypatch that raises if called.
- [x] 5.6 `discover_scans` smoke (manual, dev-machine only — same non-CI-gate note as 3.1): after
      the happy-path run, `sleap_roots_predict.discover_scans` on `out` finds one scan (same
      `pytest.importorskip` guard as 3.1).
- [x] 5.7 Registration smoke: `CliRunner().invoke(cli, ["cyl", "--help"])` output contains
      `"download-for-predict"`.
- [x] 5.8 Missing credentials → non-zero exit, "login" in output (mirror
      `test_cli_missing_credentials_hints_login`'s pattern of monkeypatching
      `creds.default_config_dir`, adapted to whichever of `load_credentials`/`_authed_client`
      `download_for_predict.py`'s command actually calls).
- [x] 5.9 Zero-frame scan: faked `fetch_images` → `[]` → exit non-zero, "no frames found" (or
      equivalent readable message) in output, no output directory created.
- [x] 5.10 `--profile` passthrough: `CliRunner().invoke(cli, ["cyl", "download-for-predict", "1",
str(out), "-p", "staging"])` with monkeypatched `load_credentials` capturing its `profile`
      argument → assert it received `"staging"`.
- [x] 5.11 Checksum changes when frame content changes: run the happy-path CLI invocation twice
      into two different output dirs with different fake bytes for the same frame; assert the two
      resulting sidecars' `images_checksum` values differ.
- [x] 5.12 Storage bucket returns `None` (not raising) for one frame: assert this is treated as a
      per-frame failure (same handling as `download.py`'s `download_images`, which raises
      `ValueError("empty response from storage")` on a `None` response) — same non-zero exit /
      failure-count / no-sidecar assertions as 5.4.
- [x] 5.13 Stale-frame reconciliation on retry (added after second review round): pre-create
      `out/scan_1/` with an extra image file not among the current run's frames (e.g. `2.png`,
      simulating a leftover from an earlier attempt whose `cyl_images` row was since deleted/
      renumbered); run the happy-path CLI invocation into that same `out`; assert `2.png` no
      longer exists after a successful run, while `0.png`/`1.png` (this run's actual frames) do.
      Verified against `sleap_roots_predict.batch._load_scan`'s real behavior (globs every
      image-extension file in the sidecar's directory, not just `image_ids`) — see design.md.

## 6. GREEN — implementation

- [x] 6.1 Create `bloomcli/src/bloomctl/cyl/download_for_predict.py`:
      Pure helpers (`scan_key_for`, `frame_dest_for_predict`, `compute_checksum`, `build_sidecar`,
      `write_sidecar`) above the `# --- supabase / storage I/O ---` marker. Import and reuse
      `fetch_scan`, `fetch_images`, `FrameResult`, `DownloadResult` from `download.py` (no
      duplication). Add `download_frames_for_predict(client, scan, images, out_dir)` → returns
      `(DownloadResult, list[bytes])` (result + per-frame bytes in frame_number order, for
      checksum). On full success (before writing the sidecar), reconcile stray files: delete any
      image-extension file in `scan_dir` that isn't one of the frame paths just written (task
      5.13; see design.md's stray-frame-reconciliation decision). Add the
      `@click.command(name="download-for-predict")` command.
- [x] 6.2 Register `download_for_predict_cmd` in `bloomcli/src/bloomctl/cyl/__init__.py`
      (alias + `cyl.add_command`, matching the pattern for `download_cmd` and `ingest_result_cmd`).
      Iterate until tasks 3–5 are GREEN.

## 7. Docs & changelog

- [x] 7.1 Add a bullet under the _existing_ `### Added` heading in `[Unreleased]` in
      `bloomcli/CHANGELOG.md` (do not add a second `### Added` header — the section already has
      one, from the `ingest-result` entry) for `bloomctl cyl download-for-predict`. Update
      `bloomcli/README.md`:
      (a) add a bullet to the top "## Commands" list, distinguishing it from `cyl download`;
      (b) add a `## bloomctl cyl download-for-predict` section matching the `ingest-result`
      section's shape (usage line, bullets of behavior, auth note, example) — positional
      args, `--profile`, output layout, sidecar field names/types/one-line purpose (full
      rationale stays in design.md, not duplicated here), and an explicit note that this
      produces a different directory tree than `cyl download` for the same scan.

## 8. Verify

- [x] 8.1 `openspec validate add-cyl-download-for-predict --strict` passes.
- [x] 8.2 bloomcli unit suite green using the CI invocation:
      `cd bloomcli && uv run --extra test pytest tests/ -m "not integration" -v --tb=short`.
      Run the release-time ruff gate manually:
      `uvx ruff@0.9.9 check bloomcli && uvx ruff@0.9.9 format --check bloomcli`.
      Run `pre-commit run --files` over new `.py`/`.md` files; confirm gitleaks is clean.
- [x] 8.3 Confirm no `TBD` or placeholder remains in `proposal.md` / `design.md`.
- [x] 8.4 Re-run task 2.1's installability check
      (`uv run --no-project --with 'sleap-roots-contracts>=0.1.0a4' python -c "from
sleap_roots_contracts import resolve_params; print('ok')"`) as a final gate, not just a
      one-off at the start of §2.
- [x] 8.5 Run `npm run contracts:check` at the repo root to confirm the re-pin (task 2.2) passes
      the drift guard before committing.
- [x] 8.6 Manually run tests 3.1 (`test_oracle_sidecar_is_accepted_by_discover_scans`) and 5.6
      (`test_discover_scans_smoke_after_happy_path`) with `sleap-roots-predict` installed locally
      and confirm they pass; paste the passing output into the PR description. **Done** — a
      pre-existing local conda env (`sleap-roots-predict-dev`) turned out to have a stale
      `sleap-nn` (`0.0.1` installed vs. the repo's own `pyproject.toml` pin of `0.3.0`) and was
      abandoned; the correct approach is `uv`, this repo's actual convention:
      `uv run --with "sleap-roots-predict[cpu] @ file:///C:/repos/sleap-roots-predict" --extra
test pytest tests/test_cyl_download_for_predict.py -k "oracle or discover_scans_smoke" -v`
      from `bloomcli/`, which builds a fresh env from `sleap-roots-predict`'s own declared `cpu`
      extra (correctly pins `sleap-nn==0.3.0` + CPU `torch`). Result:
      `2 passed, 22 deselected in 24.46s` — both oracle tests pass against the real
      `sleap_roots_predict.discover_scans`.

## 9. RED — PR #458 `/review-pr` fixes (write tests FIRST; must fail before implementation)

`/review-pr` on the merged PR reproduced three real bugs (not hypothetical) plus several
important gaps. See `design.md`'s Decisions/Risks for the full rationale of each fix. All new
tests below must FAIL against the current `download_for_predict.py` before the §10 implementation
lands.

- [x] 9.1 **BLOCKING fix — uncaught `ValueError` + reconcile-before-validation ordering:** a scan
      with `species_name=None` (or `plant_age_days=None`) exits non-zero with a readable
      `click.ClickException` message (not a raw traceback); if `scan_dir` already existed with
      content, that content is untouched (the failure happens before any destructive action).
- [x] 9.2 **New pure helper `resolve_sidecar_params(scan) -> dict`:** extracted from
      `build_sidecar`'s inline `resolve_params(scan, overrides={"mode": "cylinder"}).values` call
      — same behavior, now independently callable/testable. Raises `ValueError` on missing
      required params (unchanged underlying behavior).
- [x] 9.3 **New pure helper `validate_frame_numbers(images) -> None`:** raises `ValueError` if any
      `image["frame_number"]` is `None`, or if two images share the same non-`None`
      `frame_number`. No-op (returns `None`) otherwise.
- [x] 9.4 **CLI-level:** a scan whose `cyl_images` rows have a null or duplicate `frame_number`
      exits non-zero with a readable message before any frame is downloaded or any existing
      `scan_dir` content is touched.
- [x] 9.5 **`build_sidecar` signature change:** now `build_sidecar(scan, images,
frame_bytes_list, params)` — accepts the already-resolved `params` dict instead of calling
      `resolve_sidecar_params` internally. Update the existing tests
      (`test_build_sidecar_assembles_all_fields_in_input_order`,
      `test_build_sidecar_passes_mode_override_to_resolve_params`, the oracle test) to pass
      `params` explicitly; `test_build_sidecar_passes_mode_override_to_resolve_params` now tests
      `resolve_sidecar_params` directly (it's the one that calls `resolve_params`) rather than
      `build_sidecar`.
- [x] 9.6 **Params values, not just keys:** the sidecar's `params["species"]` is exactly
      `"pennycress"` and `params["age"]` is exactly `14` for the `SCAN` fixture (not just "keys
      present") — closes the spec.md "canonical values" scenario that had no real assertion behind
      it.
- [x] 9.7 **`clear_scan_dir(scan_dir) -> list[str]`** (replaces `reconcile_stray_frames`): if
      `scan_dir` exists, removes it entirely (frames + any old sidecar) and returns the list of
      removed entry names (for the CLI to echo); no-op, returns `[]`, if `scan_dir` doesn't exist.
- [x] 9.8 **CLI-level — stale sidecar cannot survive a retry:** run the happy-path CLI to success
      (sidecar + frames written), then re-run for the same scan with one frame now failing —
      assert the now-non-zero-exit run's directory has **no** `scan_metadata.json` at all (not a
      stale one from the first run) and reports what it cleared before starting.
- [x] 9.9 **CLI-level — clear is echoed, not silent:** the happy-path re-run test above (or a
      dedicated one) asserts the command's output mentions the directory was cleared/what was
      removed, closing the "silent deletion" gap.
- [x] 9.10 **`frame_dest_for_predict` fails loudly on a missing `object_path`:** `KeyError` (not a
      silently-defaulted `.png`) for `{"frame_number": 3}` (no `object_path` key) — still caught
      per-frame by `download_frames_for_predict`'s existing `try/except Exception`, so this
      surfaces as a clean per-frame failure, not a crash; update
      `test_frame_dest_for_predict_defaults_to_png_when_extension_missing` (which used a row that
      _had_ `object_path` with no extension — still valid, keep it) and add a new test for the
      missing-key case.
- [x] 9.11 **Atomic writes:** `write_sidecar` and each frame write survive a simulated kill
      mid-write without leaving a truncated file at the final path — assert via monkeypatching the
      write call to raise partway through, then check the final path either doesn't exist or has
      the complete prior content (never partial new content).
- [x] 9.12 **Auth refactor has no behavior change:** `test_cli_missing_credentials_hints_login`
      continues to pass unmodified after switching to `cli._authed_client` (same error message,
      same "run `bloomctl login`" hint) — this is the test that pins the refactor is behavior-
      preserving.
- [x] 9.13 **Reuse identity tests for the remaining three re-used objects** (mirrors 5.1's
      `fetch_scan` pattern): `dfp.fetch_images is dl.fetch_images`, `dfp.FrameResult is
dl.FrameResult`, `dfp.DownloadResult is dl.DownloadResult`.
- [x] 9.14 **Oracle test — `_IMAGE_EXTENSIONS` drift guard (manual, dev-machine only, same
      non-CI-gate note as §3):** add `assert dfp._IMAGE_EXTENSIONS ==
frozenset(sleap_roots_predict.batch._IMAGE_EXTENSIONS)` to
      `test_oracle_sidecar_is_accepted_by_discover_scans`, guarded by the same
      `pytest.importorskip("sleap_roots_predict")`.

## 10. GREEN — implement the fixes

- [x] 10.1 Update `bloomcli/src/bloomctl/cyl/download_for_predict.py`:
      add `resolve_sidecar_params`, `validate_frame_numbers`; change `build_sidecar`'s signature;
      rename/rewrite `reconcile_stray_frames` → `clear_scan_dir`; switch
      `frame_dest_for_predict` to `image["object_path"]`; add atomic writes (temp file +
      `os.replace`) to `write_sidecar` and the per-frame write loop; switch the command to
      `from ..cli import _authed_client`. Reorder the command body: auth → `fetch_scan` → not-found
      check → `fetch_images` → no-images check → `validate_frame_numbers` → `resolve_sidecar_params`
      (both wrapped in `try/except ValueError` → `ClickException`) → `clear_scan_dir` (echo what
      was removed) → download frames → failure check → `build_sidecar` (using the already-resolved
      `params`) → `write_sidecar` → success echo.
- [x] 10.2 Iterate until every §9 test is GREEN, and the full suite (§8.2's invocation) stays
      green with no regressions.

## 11. Re-verify

- [x] 11.1 Re-run §8.1-8.5 (validate, full suite, ruff, pre-commit, contracts guards).
- [x] 11.2 Re-run §8.6's manual oracle-test verification (now including the new
      `_IMAGE_EXTENSIONS` assertion from 9.14) — paste the passing output.
- [ ] 11.3 Push a fixup and reply to the `/review-pr` findings on PR #458, noting what was fixed
      vs. explicitly accepted as a documented limitation (concurrent same-scan invocations).
