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
unregistered command fails CLI-invocation tests outright, not silently). Land as:
1. `docs(openspec): add proposal for cyl download-for-predict (#411)` — §1 (this proposal).
2. `chore(contracts): bump sleap-roots-contracts to v0.1.0a4 (bloomcli floor + full re-pin)` —
   §2, verified installable (2.1) and re-pin diff confirmed `$id`-only (2.2) before committing.
3. `feat(bloomcli): add download_for_predict pure helpers + oracle test (#411)` — §3 (oracle test)
   + §4 (pure helpers) + the pure-helper half of §6.1 (no CLI registration yet).
4. `feat(bloomctl): register cyl download-for-predict command (#411)` — §5 (command-wiring tests)
   + the I/O/CLI half of §6.1 + §6.2 (registration).
5. `docs(bloomcli): document cyl download-for-predict command` — §7.
§8 (verify) is a gate applied across all commits during review, not its own commit.

## 1. Proposal & specs (this change)

- [x] 1.1 Author `proposal.md`, `design.md`, and the `cyl-download-for-predict` spec delta.
      Run `openspec validate add-cyl-download-for-predict --strict` and resolve all issues.

## 2. Package + repo setup (pre-req; lands before tests that import it)

- [ ] 2.1 Bump `sleap-roots-contracts>=0.1.0a3` → `>=0.1.0a4` in `bloomcli/pyproject.toml`.
      Verify installability: `uv run --no-project --with 'sleap-roots-contracts>=0.1.0a4' python -c
      "from sleap_roots_contracts import resolve_params; print('ok')"`. bloomcli has no `uv.lock` —
      no lock refresh needed.
- [ ] 2.2 Full contracts re-pin per contracts PR #16 instructions:
      (a) update `contracts/pin.json`: version `v0.1.0a3` → `v0.1.0a4`, `$id` and `source` URLs;
      (b) fetch updated `contracts/schema/result_envelope.schema.json` from
          `github.com/talmolab/sleap-roots-contracts` at tag `v0.1.0a4` and **diff it against the
          currently-vendored `a3` schema with the version string normalized out** — do not assume
          the diff shape in advance (the prior two re-pins, `a1→a2` and `a2→a3`, were both real
          revisions, not `$id`-only no-ops). Confirmed during review: for `a4` this diff IS
          `$id`-only;
      (c) regenerate `contracts/generated/result-envelope.ts` from the updated schema (`npm run
          contracts:gen`, per `contracts/README.md`'s re-pin procedure) and run
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
CI**; run it manually on a dev machine with `sleap-roots-predict` installed (e.g. via the local
`sleap-roots-predict-dev` environment) before merge. Tasks 4.x/5.x below independently assert
every shape fact predict's code is documented to require, without importing predict — that suite,
not this test, is the CI-enforced contract for this change.

- [ ] 3.1 **Oracle / acceptance test (manual, dev-machine only — see note above):**
      Given the SCAN fixture row (from `test_download_metadata.py`), a list of two fake
      `cyl_images` rows (`id=1001, frame_number=0, object_path="cyl-images/a.png"` and `id=1002,
      frame_number=1, object_path="cyl-images/b.png"`), and fake frame bytes per image:
      - `build_sidecar(scan, images, frame_bytes)` returns a dict whose `scan_key` equals
        `"scan_1"` (scan_id=1 from SCAN fixture);
      - the dict's `params` has keys `species`, `mode`, `age`; `mode` is `"cylinder"`;
      - the dict's `image_ids` is `[1001, 1002]`;
      - the dict's `images_checksum` starts with `"sha256:"`;
      - **cross-repo oracle**: write the sidecar to `tmp_path/scan_1/scan_1.scan_metadata.json`
        alongside two fake image files, then assert
        `sleap_roots_predict.discover_scans(tmp_path)` returns exactly one `ScanInput` with
        `scan_key="scan_1"`, `error=None`, and non-None `params`
        (`pytest.importorskip("sleap_roots_predict")` at the top of the test).

## 4. RED — pure helpers (`bloomcli/tests/test_cyl_download_for_predict.py`)

Each test must FAIL before `download_for_predict.py` exists.

- [ ] 4.1 `scan_key_for(scan_id)` returns `f"scan_{scan_id}"` for integer and string inputs.
- [ ] 4.2 `frame_dest_for_predict(scan_dir, image)` returns
      `scan_dir / f"{image['frame_number']}{suffix}"` where suffix is the original extension of
      `image['object_path']` (or `.png` when missing).
- [ ] 4.3 `compute_checksum(frame_bytes_list)` returns a string starting with `"sha256:"` whose
      hex suffix is the sha256 of all bytes concatenated in list order; an empty list produces a
      well-defined checksum (sha256 of `b""`).
- [ ] 4.4 `build_sidecar(scan, images, frame_bytes_list)` assembles the sidecar dict — all four
      fields present (`scan_key`, `params`, `image_ids`, `images_checksum`); `image_ids` order
      matches the input `images` list order; `scan_key` equals `scan_key_for(scan["scan_id"])`.
- [ ] 4.5 `build_sidecar` calls `resolve_params(scan, overrides={"mode": "cylinder"})` — assert
      the actual call, not just the output (monkeypatch/spy on `resolve_params` to capture the
      `overrides` argument it receives), and assert it is exactly `{"mode": "cylinder"}`.
      (Asserting only `params["mode"] == "cylinder"` is insufficient: `sleap-roots-contracts
      >=0.1.0a4`'s `_mode_for_scan` currently ignores its `metadata` argument and unconditionally
      returns `"cylinder"` regardless of how — or whether — `resolve_params` is called, so that
      assertion alone can't distinguish "the override was passed" from "the oracle ignores every
      caller.")
- [ ] 4.5b Add `test_resolve_params_ignores_mode_on_pinned_contracts_version`: call
      `resolve_params(SCAN)` directly (no `overrides` at all) against the pinned
      `sleap-roots-contracts>=0.1.0a4`, and assert `result.values["mode"] == "cylinder"` anyway.
      Documents that `overrides={"mode": "cylinder"}` in `build_sidecar` is not currently
      load-bearing on this contracts version; if a future contracts bump makes `mode`
      metadata-driven, this test starts failing and flags the divergence instead of it going
      unnoticed.
- [ ] 4.6 `write_sidecar(sidecar, path)` writes valid UTF-8 JSON to `path` (parent created if
      absent); round-trips back to the original dict via `json.loads`.
- [ ] 4.7 `frame_dest_for_predict` with `image["object_path"]` lacking an extension (e.g.
      `"cyl-images/a"`) returns a path ending in `.png` (the documented default).
- [ ] 4.8 `compute_checksum` and `build_sidecar` with an empty `images` / `frame_bytes_list`
      produce `image_ids: []` and `images_checksum == "sha256:" + hashlib.sha256(b"").hexdigest()`
      — documents the pure helpers are well-defined on empty input. (Note: this path is not
      actually exercised by 5.9's CLI behavior — the command short-circuits on zero `cyl_images`
      rows before ever calling `build_sidecar`, per spec.md's "zero cyl_images rows" scenario —
      this task exists for the helpers' own contract, independent of that CLI-level guard.)

## 5. RED — command wiring (`bloomcli/tests/test_cyl_download_for_predict.py`)

- [ ] 5.1 Confirm `fetch_scan` is reused from `download.py`, not duplicated: import
      `bloomctl.cyl.download_for_predict` and assert `download_for_predict.fetch_scan is
      download.fetch_scan` (mirrors the intent of `test_fetch_scan_returns_single_row`, which
      exercises `fetch_scan` itself — this task instead pins the no-duplication contract).
- [ ] 5.2 Happy-path CLI: `CliRunner().invoke(cli, ["cyl", "download-for-predict", "1",
      str(out)])` with faked `fetch_scan` → SCAN, faked `fetch_images` → two image rows, faked
      Storage bucket, monkeypatched creds/auth → exit 0; assert:
      - `out/scan_1/0.png` and `out/scan_1/1.png` exist;
      - `out/scan_1/scan_1.scan_metadata.json` is valid JSON with `scan_key="scan_1"`,
        `image_ids=[1001, 1002]`, `params.mode="cylinder"`, `images_checksum` starts with
        `"sha256:"`.
- [ ] 5.3 Scan not found: faked `fetch_scan` returns `None` → exit non-zero, "not found" in
      output, no directory created.
- [ ] 5.4 Partial frame failure: one frame download raises → exit non-zero, failure count in
      output; assert the successfully-downloaded frame file(s) still exist on disk; assert
      `scan_1.scan_metadata.json` is **not** written (per the design decision: a sidecar is a
      claim that `image_ids`/`images_checksum` match what's on disk — see design.md).
- [ ] 5.5 `fetch_scans` (experiment path) is never called in `download-for-predict` — assert via
      monkeypatch that raises if called.
- [ ] 5.6 `discover_scans` smoke (manual, dev-machine only — same non-CI-gate note as 3.1): after
      the happy-path run, `sleap_roots_predict.discover_scans` on `out` finds one scan (same
      `pytest.importorskip` guard as 3.1).
- [ ] 5.7 Registration smoke: `CliRunner().invoke(cli, ["cyl", "--help"])` output contains
      `"download-for-predict"`.
- [ ] 5.8 Missing credentials → non-zero exit, "login" in output (mirror
      `test_cli_missing_credentials_hints_login`'s pattern of monkeypatching
      `creds.default_config_dir`, adapted to whichever of `load_credentials`/`_authed_client`
      `download_for_predict.py`'s command actually calls).
- [ ] 5.9 Zero-frame scan: faked `fetch_images` → `[]` → exit non-zero, "no frames found" (or
      equivalent readable message) in output, no output directory created.
- [ ] 5.10 `--profile` passthrough: `CliRunner().invoke(cli, ["cyl", "download-for-predict", "1",
      str(out), "-p", "staging"])` with monkeypatched `load_credentials` capturing its `profile`
      argument → assert it received `"staging"`.
- [ ] 5.11 Checksum changes when frame content changes: run the happy-path CLI invocation twice
      into two different output dirs with different fake bytes for the same frame; assert the two
      resulting sidecars' `images_checksum` values differ.
- [ ] 5.12 Storage bucket returns `None` (not raising) for one frame: assert this is treated as a
      per-frame failure (same handling as `download.py`'s `download_images`, which raises
      `ValueError("empty response from storage")` on a `None` response) — same non-zero exit /
      failure-count / no-sidecar assertions as 5.4.
- [ ] 5.13 Stale-frame reconciliation on retry (added after second review round): pre-create
      `out/scan_1/` with an extra image file not among the current run's frames (e.g. `2.png`,
      simulating a leftover from an earlier attempt whose `cyl_images` row was since deleted/
      renumbered); run the happy-path CLI invocation into that same `out`; assert `2.png` no
      longer exists after a successful run, while `0.png`/`1.png` (this run's actual frames) do.
      Verified against `sleap_roots_predict.batch._load_scan`'s real behavior (globs every
      image-extension file in the sidecar's directory, not just `image_ids`) — see design.md.

## 6. GREEN — implementation

- [ ] 6.1 Create `bloomcli/src/bloomctl/cyl/download_for_predict.py`:
      Pure helpers (`scan_key_for`, `frame_dest_for_predict`, `compute_checksum`, `build_sidecar`,
      `write_sidecar`) above the `# --- supabase / storage I/O ---` marker. Import and reuse
      `fetch_scan`, `fetch_images`, `FrameResult`, `DownloadResult` from `download.py` (no
      duplication). Add `download_frames_for_predict(client, scan, images, out_dir)` → returns
      `(DownloadResult, list[bytes])` (result + per-frame bytes in frame_number order, for
      checksum). On full success (before writing the sidecar), reconcile stray files: delete any
      image-extension file in `scan_dir` that isn't one of the frame paths just written (task
      5.13; see design.md's stray-frame-reconciliation decision). Add the
      `@click.command(name="download-for-predict")` command.
- [ ] 6.2 Register `download_for_predict_cmd` in `bloomcli/src/bloomctl/cyl/__init__.py`
      (alias + `cyl.add_command`, matching the pattern for `download_cmd` and `ingest_result_cmd`).
      Iterate until tasks 3–5 are GREEN.

## 7. Docs & changelog

- [ ] 7.1 Add a bullet under the *existing* `### Added` heading in `[Unreleased]` in
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

- [ ] 8.1 `openspec validate add-cyl-download-for-predict --strict` passes.
- [ ] 8.2 bloomcli unit suite green using the CI invocation:
      `cd bloomcli && uv run --extra test pytest tests/ -m "not integration" -v --tb=short`.
      Run the release-time ruff gate manually:
      `uvx ruff@0.9.9 check bloomcli && uvx ruff@0.9.9 format --check bloomcli`.
      Run `pre-commit run --files` over new `.py`/`.md` files; confirm gitleaks is clean.
- [ ] 8.3 Confirm no `TBD` or placeholder remains in `proposal.md` / `design.md`.
- [ ] 8.4 Re-run task 2.1's installability check
      (`uv run --no-project --with 'sleap-roots-contracts>=0.1.0a4' python -c "from
      sleap_roots_contracts import resolve_params; print('ok')"`) as a final gate, not just a
      one-off at the start of §2.
- [ ] 8.5 Run `npm run contracts:check` at the repo root to confirm the re-pin (task 2.2) passes
      the drift guard before committing.
- [ ] 8.6 Manually run tests 3.1 and 5.6 with `sleap-roots-predict` installed locally (e.g. the
      `sleap-roots-predict-dev` environment) and confirm they pass; paste the passing output into
      the PR description — this is the only place these two tests are demonstrated to pass, since
      they self-skip in CI (see §3 note).
